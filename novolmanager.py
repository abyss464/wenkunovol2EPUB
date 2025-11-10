import time
import requests
import os
import random
import re
import concurrent.futures
import hashlib
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urlparse

try:
    from ebooklib import epub
except ImportError:
    print("错误: 未找到 EbookLib 库。请先安装: pip install EbookLib")
    exit()

try:
    import config
except ImportError:
    print("错误：无法找到 config.py 文件。")
    exit()


# ===============================================================
# 哈希校验与增量更新函数 (新增)
# ===============================================================

def get_file_sha256(filepath):
    """计算文件的SHA256哈希值"""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def save_hashes(hash_file_path, illustration_data):
    """保存插图的哈希信息到文件"""
    hashes = {}
    for volume, paths in illustration_data.items():
        hashes[volume] = {os.path.basename(p): get_file_sha256(p) for p in paths}
    with open(hash_file_path, 'w', encoding='utf-8') as f:
        json.dump(hashes, f, ensure_ascii=False, indent=4)
    print(f"插图哈希信息已保存至: {hash_file_path}")

def check_hashes(hash_file_path, download_dir):
    """检查本地插图文件与哈希文件是否一致"""
    if not os.path.exists(hash_file_path):
        return False, None

    with open(hash_file_path, 'r', encoding='utf-8') as f:
        saved_hashes = json.load(f)

    current_illustrations = {}
    all_match = True

    for volume, files in saved_hashes.items():
        volume_path = os.path.join(download_dir, volume)
        if not os.path.isdir(volume_path):
            return False, None  # 卷文件夹不存在

        current_illustrations[volume] = []
        for filename, filehash in files.items():
            filepath = os.path.join(volume_path, filename)
            if not os.path.exists(filepath) or get_file_sha256(filepath) != filehash:
                all_match = False
                break
            current_illustrations[volume].append(filepath)
        if not all_match:
            break

    if all_match:
        print("哈希校验通过，本地插图文件完整且未变动。")
        return True, current_illustrations
    else:
        print("哈希校验失败或文件不完整，需要重新下载插图。")
        return False, None


def create_epub(book_title, metadata, txt_file_path, illustration_data, output_dir, cover_path=None):
    """
    1.  修复了因正则表达式嵌套捕获组导致 re.split 结果错位，将正文识别为标题的严重错误。
    2.  保留了v4版本的所有优点（健壮循环、精准章节、清爽目录等）。
    3.  彻底解决了 EpubLib 封面、NCX、Nav 重复添加的问题。
    4.  修复了 'EpubImage' object has no attribute 'uid' 错误，通过更健壮的封面处理逻辑。
    """
    print("\n" + "=" * 15 + " 开始创建EPUB文件" + "=" * 15)
    safe_book_title = re.sub(r'[\\/*?:"<>|]', '', book_title)
    epub_file_path = os.path.join(output_dir, f"{safe_book_title}.epub")

    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{safe_book_title}-{int(time.time())}")
    book.set_title(book_title)
    book.set_language('zh')
    book.add_author(metadata.get('author', '未知'))

    # --- 1. 处理并添加所有资源（封面、插图）---
    cover_page_item = None
    cover_image_item = None

    if cover_path and os.path.exists(cover_path):
        print("正在处理封面...")
        try:
            with open(cover_path, 'rb') as f:
                cover_content = f.read()

            # 定义封面图片在EPUB内部的路径和文件名
            cover_internal_filename = 'images/cover.jpg'

            # 1. 创建 EpubImage 对象
            cover_image_item = epub.EpubImage(
                uid='cover_image',
                file_name=cover_internal_filename,
                media_type='image/jpeg',  # 假设是JPEG
                content=cover_content
            )

            # 2. 将创建的图片对象添加到书中
            book.add_item(cover_image_item)

            # 3. **关键修复点**：将这个图片项目标记为封面。
            #    EbookLib 会自动为封面图片添加 'cover-image' 属性。
            #    我们通过直接设置 book.cover_page 属性来指定哪个 item 是封面图片。
            #    这比调用 set_cover() 更底层，也避免了其自动创建行为。
            book.cover_page = cover_image_item
            print(f"封面图片 '{cover_internal_filename}' 已成功添加并标记为EPUB封面。")

            # 4. 手动创建一个独立的 XHTML 页面来展示封面图片
            cover_page_item = epub.EpubHtml(title='封面', file_name='cover.xhtml', lang='zh')
            cover_page_item.content = f'''
                <html xmlns="http://www.w3.org/1999/xhtml">
                <head>
                    <title>封面</title>
                    <style>
                        body {{ margin:0; padding:0; text-align:center; }}
                        img {{ max-width:100%; max-height:100vh; object-fit:contain; }}
                    </style>
                </head>
                <body>
                    <div><img src="{cover_internal_filename}" alt="封面"/></div>
                </body>
                </html>
            '''
            book.add_item(cover_page_item)
            print(f"封面XHTML页面 '{cover_page_item.file_name}' 已手动创建并添加。")

        except Exception as e:
            print(f"警告: 处理封面时发生错误: {e}")
            cover_image_item = None
            cover_page_item = None
    else:
        print("未找到有效封面路径或文件不存在，跳过封面处理。")

    illustration_items = {}
    if illustration_data:
        print("正在添加所有插图资源...")
        for volume, paths in illustration_data.items():
            for img_path in paths:
                try:
                    with open(img_path, 'rb') as f:
                        img_content = f.read()
                    safe_volume = re.sub(r'[\\/*?:"<>|]', '', volume)
                    img_filename = f'images/{safe_volume}/{os.path.basename(img_path)}'
                    ext = os.path.splitext(img_path)[1].lower()
                    media_type = 'image/jpeg' if ext in ['.jpg', '.jpeg'] else 'image/png'
                    img_item = epub.EpubImage(uid=f'img_{safe_volume}_{os.path.basename(img_path)}',
                                              file_name=img_filename, media_type=media_type, content=img_content)
                    book.add_item(img_item)
                    illustration_items[img_path] = img_item
                except Exception as e:
                    print(f"警告: 添加图片资源 {img_path} 失败: {e}")

    # --- 2. 读取并拆分文本内容 ---
    try:
        with open(txt_file_path, 'r', encoding='gbk', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"错误：读取TXT文件失败: {e}")
        return

    # 辅助函数
    def get_core_volume_name(title):
        core_match = re.match(r'(第[一二三四五六七八九十百]+卷|短篇|SS\d*|特典)', title)
        return core_match.group(0) if core_match else title

    core_illustration_map = {get_core_volume_name(k): v for k, v in
                             illustration_data.items()} if illustration_data else {}

    spine_items = []
    # 如果存在封面XHTML页面，则将其作为书脊的第一项，使其成为读者打开书时看到的第一页。
    if cover_page_item:
        spine_items.append(cover_page_item)
    spine_items.append('nav')  # 'nav' 是 EbookLib 的一个特殊字符串，代表导航页

    final_toc = []

    # --- 3. 生成内容章节 (简介、序章、各卷) ---
    # 简介
    synopsis_chapter = epub.EpubHtml(title='简介', file_name='synopsis.xhtml', lang='zh')
    synopsis_content = metadata.get("synopsis", "无").replace("\n", "</p><p>").replace("　　", "")
    synopsis_chapter.content = f'<h1>简介</h1><p>{synopsis_content}</p>'
    book.add_item(synopsis_chapter)
    spine_items.append(synopsis_chapter)
    final_toc.append(epub.Link('synopsis.xhtml', '简介', 'synopsis'))

    # 按卷拆分
    known_volume_titles = list(illustration_data.keys()) if illustration_data else []
    generic_titles = [r'第[一二三四五六七八九十百]+卷', r'短篇', r'SS\d*', r'特典']
    all_titles_pattern = '|'.join([re.escape(title) for title in known_volume_titles] + generic_titles)
    split_pattern = f'^((?:{all_titles_pattern}).*)$'
    parts = re.split(split_pattern, content, flags=re.MULTILINE)

    prologue_content = parts[0].strip()
    if prologue_content:
        html_content = ''.join([f'<p>{line.strip()}</p>' for line in prologue_content.splitlines() if line.strip()])
        prologue_chapter = epub.EpubHtml(title='序章', file_name='prologue.xhtml',
                                         content=f"<h1>序章</h1>{html_content}")
        book.add_item(prologue_chapter)
        spine_items.append(prologue_chapter)
        final_toc.append(prologue_chapter)

    volume_titles = parts[1::2]
    volume_contents = parts[2::2]
    for volume_index, (volume_title, volume_text) in enumerate(zip(volume_titles, volume_contents), 1):
        volume_title = volume_title.strip()
        volume_text = volume_text.strip()
        volume_sub_toc = []
        current_core_title = get_core_volume_name(volume_title)

        # 优先处理插图页
        if current_core_title in core_illustration_map:
            print(f"为《{volume_title}》创建插图页面...")
            image_paths = core_illustration_map.pop(current_core_title)
            illust_xhtml_name = f'illust_{volume_index}.xhtml'
            illust_chapter = epub.EpubHtml(title=f'{volume_title} 插图', file_name=illust_xhtml_name)
            img_html_parts = []
            for img_path in image_paths:
                if img_path in illustration_items:
                    img_item = illustration_items[img_path]
                    img_html_parts.append(
                        f'<div class="illustration"><img src="{img_item.file_name}" alt="插图"/></div>')
            if img_html_parts:
                illust_chapter.content = ''.join(img_html_parts)
                book.add_item(illust_chapter)
                spine_items.append(illust_chapter)

        # 清理文本中的“插图”字样
        volume_text = re.sub(r'.*插图.*', '', volume_text)
        sub_chapter_pattern = r'^(第[一二三四五六七八九十零\d]+[章话节].*|终章|序章|后记|Epilogue|Prologue)$'
        sub_parts = re.split(sub_chapter_pattern, volume_text, flags=re.MULTILINE)
        volume_intro_text = sub_parts[0].strip()
        sub_titles = sub_parts[1::2]
        sub_contents = sub_parts[2::2]

        if not sub_titles:
            if volume_text.strip():
                lines = [f'<p>{line.strip()}</p>' for line in volume_text.splitlines() if
                         line.strip() and line.strip() != volume_title]
                html_content = ''.join(lines)
                chapter_obj = epub.EpubHtml(title=volume_title, file_name=f'vol_{volume_index}_full.xhtml')
                chapter_obj.content = f'<h1>{volume_title}</h1>{html_content}'
                book.add_item(chapter_obj)
                spine_items.append(chapter_obj)
                final_toc.append(chapter_obj)
            continue

        for sub_chapter_index, (sub_title, sub_text) in enumerate(zip(sub_titles, sub_contents), 1):
            sub_title = sub_title.strip()
            sub_text_full = (volume_intro_text if sub_chapter_index == 1 else "") + sub_text.strip()
            lines = [f'<p>{line.strip()}</p>' for line in sub_text_full.splitlines() if line.strip()]
            html_content = ''.join(lines)
            file_name = f'vol_{volume_index}_chap_{sub_chapter_index}.xhtml'
            chapter_obj = epub.EpubHtml(title=sub_title, file_name=file_name)
            chapter_obj.content = f'<h1>{sub_title}</h1>{html_content}'
            book.add_item(chapter_obj)
            spine_items.append(chapter_obj)
            volume_sub_toc.append(chapter_obj)

        if volume_sub_toc:
            final_toc.append((epub.Section(volume_title), tuple(volume_sub_toc)))

    # --- 4. 组装EPUB ---
    book.toc = tuple(final_toc)
    book.spine = spine_items

    # 添加CSS样式
    style = '''
    body { font-family: Times, serif; }
    h1, h2 { text-align: center; font-weight: bold; }
    p { text-indent: 2em; margin: 0; padding: 0; line-height: 1.6; }
    .illustration { text-align: center; margin: 1em 0; page-break-before: always; }
    img { max-width: 100%; height: auto; display: block; margin-left: auto; margin-right: auto; }
    '''
    nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style)
    book.add_item(nav_css)

    # 添加导航文件
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    print("正在写入EPUB文件...")
    epub.write_epub(epub_file_path, book, {})
    print(f"EPUB文件已成功创建: {epub_file_path}")


def scrape_metadata(driver):
    """在小说详情页抓取作者、简介和封面URL"""
    metadata = {'author': '未知', 'synopsis': '无', 'cover_url': None}
    print("正在抓取小说元数据（作者、简介、封面）...")

    # 抓取作者
    try:
        author_element = driver.find_element(By.XPATH, "//td[contains(text(), '小说作者：')]")
        metadata['author'] = author_element.text.replace('小说作者：', '').strip()
        print(f"作者: {metadata['author']}")  # 确认作者
    except NoSuchElementException:
        print("警告：未找到作者信息。")

    # 抓取简介
    try:
        synopsis_element = driver.find_element(By.XPATH,
                                               "//span[contains(text(), '内容简介：')]/following-sibling::span[1]")
        metadata['synopsis'] = synopsis_element.text.strip()
        # 简介内容可能很长，不直接打印，只确认抓取到
        print(f"简介: {'已抓取' if metadata['synopsis'] else '未抓取到'} (长度: {len(metadata['synopsis'])} 字)")
    except NoSuchElementException:
        print("警告：未找到简介信息。")

    # --- [新增功能] 抓取封面图片URL ---
    try:
        # 原始XPath：//td[@width='20%']//img
        # 尝试使用更常见的封面图片元素路径，例如根据ID或更通用的class
        # 可以在浏览器开发者工具中，审查元素，找到封面图片的准确XPath或CSS选择器
        # 临时改为更通用或直接的XPath进行测试

        # 建议检查以下几种XPath，选择最适合当前网站结构的
        # cover_image_element = driver.find_element(By.XPATH, "//div[@id='fmimg']/img") # 常见ID
        # cover_image_element = driver.find_element(By.XPATH, "//div[@class='cover']/img") # 常见Class
        # cover_image_element = driver.find_element(By.XPATH, "//div[@class='book-info-cover']//img") # 另一种常见Class

        # 为了兼容性，我们可以尝试多种，或者坚持原来的，但要知道它可能需要更新
        # 这里先沿用你原来的，但请留意如果问题依然存在，这里是需要优先检查的地方
        cover_image_element = driver.find_element(By.XPATH, "//td[@width='20%']//img")

        metadata['cover_url'] = cover_image_element.get_attribute('src')
        if metadata['cover_url']:
            print(f"成功获取封面URL: {metadata['cover_url']}")
        else:
            print("警告：获取到的封面URL为空。")
            metadata['cover_url'] = None  # 确保为空时为None
    except NoSuchElementException:
        print("警告：未找到封面图片元素。XPath可能需要更新。")
        metadata['cover_url'] = None  # 确保未找到时为None
    except Exception as e:
        print(f"警告：抓取封面URL时发生未知错误: {e}")
        metadata['cover_url'] = None  # 确保异常时为None
    # --- [新增功能结束] ---

    return metadata

# ===============================================================
# 总指挥与下载函数 (集成哈希校验逻辑)
# ===============================================================
def find_and_download_novel(driver, book_title):
    print("\n" + "=" * 20 + f" 开始处理书籍: {book_title} " + "=" * 20)

    safe_book_title = re.sub(r'[\\/*?:"<>|]', '', book_title)
    download_dir = os.path.join("download", safe_book_title)
    hash_file = os.path.join(download_dir, ".hash")
    os.makedirs(download_dir, exist_ok=True)

    illustrations_ok, illustration_data = check_hashes(hash_file, download_dir)

    original_window = driver.current_window_handle
    if not search_for_novel(driver, book_title): return

    cover_image_path = None  # 初始化封面路径变量

    try:
        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
        novel_window = [window for window in driver.window_handles if window != original_window][0]
        driver.switch_to.window(novel_window)

        metadata = scrape_metadata(driver)

        # --- [新增功能] 下载封面 ---
        if metadata.get('cover_url'):
            cover_image_path = os.path.join(download_dir, 'cover.jpg')
            print(f"正在尝试下载封面至: {cover_image_path}")
            try:
                # 假设封面不需要复杂的Referer，但加上requests headers总没错
                # 使用一个简单的下载逻辑
                response = requests.get(metadata['cover_url'], timeout=30, headers={'User-Agent': 'Mozilla/5.0 ...'})
                if response.ok:
                    with open(cover_image_path, 'wb') as f:
                        f.write(response.content)
                    print(f"封面下载成功: {cover_image_path}")
                else:
                    print(f"封面下载失败，HTTP状态码: {response.status_code}")
                    cover_image_path = None  # 下载失败则重置
            except requests.exceptions.RequestException as e:
                print(f"封面下载请求失败 (网络错误/超时): {e}")
                cover_image_path = None
            except Exception as e:
                print(f"封面下载时发生未知错误: {e}")
                cover_image_path = None
        else:
            print("未从页面抓取到封面图片URL，跳过封面下载。")
            cover_image_path = None  # 明确设置为None

        # --- [新增功能结束] ---

        parsed_url = urlparse(driver.current_url)
        # 确保novel_id能够正确提取，避免索引错误
        path_segments = parsed_url.path.strip('/').split('/')
        novel_id = path_segments[1].replace('.htm', '') if len(path_segments) > 1 else None

        if not novel_id:
            print(f"错误：无法从URL {driver.current_url} 提取小说ID，无法下载TXT。")
            return

        if not illustrations_ok:
            illustration_data = download_illustrations(driver, book_title)
            if illustration_data:
                save_hashes(hash_file, illustration_data)
        else:
            print("插图哈希校验通过，跳过插图下载。")


    except Exception as e:
        print(f"处理小说详情页或插图时发生严重错误: {e}")
        return
    finally:
        # 确保关闭所有新打开的窗口，只保留原始窗口
        while len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])  # 切换到最后一个窗口
            driver.close()  # 关闭当前窗口
        driver.switch_to.window(original_window)  # 切换回原始窗口

    txt_save_path = os.path.join(download_dir, f"{safe_book_title}.txt")

    # 检查TXT文件是否存在且非空，作为下载成功的标志
    txt_success = False
    if os.path.exists(txt_save_path) and os.path.getsize(txt_save_path) > 100:  # 认为小于100字节的文件可能是空文件或错误文件
        print(f"本地已存在TXT文件: {txt_save_path}，跳过下载。")
        txt_success = True
    else:
        # 使用refer_url
        referer = f"https://www.wenku8.net/modules/article/packshow.php?id={novel_id}&type=txtfull"
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}

        print(f"尝试下载TXT文件（Node=1）...")
        txt_success = download_txt(novel_id, 1, txt_save_path, cookies, referer)
        if not txt_success:
            print(f"TXT文件（Node=1）下载失败或内容异常，尝试下载TXT文件（Node=2）...")
            txt_success = download_txt(novel_id, 2, txt_save_path, cookies, referer)

    if not txt_success:
        print(f"《{book_title}》TXT 文件下载失败，无法创建EPUB。")
        return

    # 将封面路径传递给 create_epub
    create_epub(book_title, metadata, txt_save_path, illustration_data, download_dir, cover_path=cover_image_path)


# ===============================================================
# 主程序入口 (修改为自动化列表处理)
# ===============================================================
def main():
    """主执行函数，自动化处理config中的小说列表"""
    if not hasattr(config, 'NOVEL_LIST') or not config.NOVEL_LIST:
        print("错误：config.py 中未找到或未定义 NOVEL_LIST。")
        return

    print("正在初始化浏览器驱动...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service)
    base_url = "https://www.wenku8.net"

    try:
        driver.get(base_url)
        if login_wenku8(driver, config.USERNAME, config.PASSWORD):
            # --- [核心修改点 3] ---
            print("\n开始自动化处理小说列表...")
            for book_title in config.NOVEL_LIST:
                # 每次循环都回到首页以保证搜索环境一致
                driver.get(base_url)
                time.sleep(1)
                find_and_download_novel(driver, book_title)
                print("-" * 50)
            print("\n所有小说处理完毕！")

    except Exception as e:
        print(f"主程序发生严重错误: {e}")
    finally:
        print("\n正在关闭浏览器...");
        driver.quit()


# 其它函数定义（为保证可运行性，复制过来）
def download_illustrations(driver, book_title):
    print("\n" + "-" * 15 + " 开始处理插图（精确模式）" + "-" * 15)
    volume_images_map = {}
    try:
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.PARTIAL_LINK_TEXT, "小说目录"))).click()
        novel_index_window = driver.current_window_handle
        table_body = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "tbody")))
        rows = table_body.find_elements(By.TAG_NAME, "tr")
        volume_to_illustration_map = {}
        current_volume_name = ""
        for row in rows:
            try:
                volume_header = row.find_element(By.CLASS_NAME, "vcss")
                current_volume_name = re.sub(r'[\\/*?:"<>|]', '', volume_header.text.strip())
            except NoSuchElementException:
                if current_volume_name:
                    for link in row.find_elements(By.TAG_NAME, "a"):
                        if "插图" in link.text: volume_to_illustration_map[current_volume_name] = link.get_attribute(
                            'href'); break
        if not volume_to_illustration_map: print("未发现插图链接。"); return {}
        download_tasks = []
        for volume_name, url in volume_to_illustration_map.items():
            delay = random.uniform(1.5, 3.5);
            time.sleep(delay)
            driver.execute_script(f"window.open('{url}');");
            driver.switch_to.window(driver.window_handles[-1])
            try:
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "imagecontent")))
                image_elements = driver.find_elements(By.CLASS_NAME, "imagecontent")
                image_srcs = [img.get_attribute('src') for img in image_elements if img.get_attribute('src')]
                if image_srcs:
                    safe_book_title = re.sub(r'[\\/*?:"<>|]', '', book_title)
                    illust_dir = os.path.join("download", safe_book_title, volume_name);
                    os.makedirs(illust_dir, exist_ok=True)
                    volume_images_map[volume_name] = []
                    current_page_url = driver.current_url
                    for j, img_src in enumerate(image_srcs):
                        file_name = os.path.basename(urlparse(img_src).path) or f"{j + 1:02d}.jpg"
                        save_path = os.path.join(illust_dir, file_name)
                        download_tasks.append((img_src, save_path, current_page_url))
                        volume_images_map[volume_name].append(save_path)
            finally:
                driver.close();
                driver.switch_to.window(novel_index_window)
        if not download_tasks: return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(lambda p: download_image(*p), download_tasks))
    except Exception as e:
        print(f"插图处理出错: {e}"); return {}
    return volume_images_map


def download_image(url, save_path, referer_url):
    try:
        proxies = {"http": None, "https": None}
        headers = {'User-Agent': 'Mozilla/5.0 ...', 'Referer': referer_url}
        response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
        if response.ok:
            with open(save_path, 'wb') as f: f.write(response.content)
    except Exception:
        pass


def login_wenku8(driver, username, password):
    print("正在尝试登录...");
    try:
        u, p, b = driver.find_element(By.NAME, "username"), driver.find_element(By.NAME,
                                                                                "password"), driver.find_element(
            By.NAME, "submit")
        u.send_keys(username);
        p.send_keys(password);
        b.click();
        time.sleep(10)
        if "欢迎您" in driver.page_source:
            print("登录成功！"); return True
        else:
            print("登录失败"); return False
    except Exception as e:
        print(f"登录时出错: {e}"); return False


def search_for_novel(driver, book_title):
    try:
        s, b = driver.find_element(By.ID, "searchkey"), driver.find_element(By.NAME, "Submit")
        s.clear();
        s.send_keys(book_title);
        b.click();
        return True
    except Exception as e:
        print(f"搜索时出错: {e}"); return False


def download_txt(novel_id, node, save_path, cookies, referer_url):
    url = f"https://dl.wenku8.com/down.php?type=txt&node={node}&id={novel_id}"
    print(f"正在从 Node={node} 下载TXT...")
    try:
        proxies = {"http": None, "https": None}
        headers = {'User-Agent': 'Mozilla/5.0 ...', 'Referer': referer_url}
        response = requests.get(url, cookies=cookies, timeout=60, proxies=proxies, headers=headers)
        if response.ok and len(response.content) > 100:
            with open(save_path, 'wb') as f: f.write(response.content)
            return True
    except Exception:
        pass
    return False


if __name__ == "__main__":
    main()
