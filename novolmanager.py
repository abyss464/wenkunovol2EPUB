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


def get_file_sha256(filepath):
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except IOError as e:
        print(f"错误: 无法读取文件 {filepath} 来计算哈希值: {e}")
        return None


def save_hashes(hash_file_path, illustration_data):
    hashes = {}
    for volume, paths in illustration_data.items():
        hashes[volume] = {}
        for p in paths:
            file_hash = get_file_sha256(p)
            if file_hash:
                hashes[volume][os.path.basename(p)] = file_hash
            else:
                print(f"警告: 文件 {p} 的哈希值计算失败，将不保存其哈希信息。")
    try:
        with open(hash_file_path, 'w', encoding='utf-8') as f:
            json.dump(hashes, f, ensure_ascii=False, indent=4)
        print(f"插图哈希信息已保存至: {hash_file_path}")
    except IOError as e:
        print(f"错误: 无法保存哈希文件 {hash_file_path}: {e}")


def check_hashes(hash_file_path, download_dir):
    if not os.path.exists(hash_file_path):
        return False, None

    try:
        with open(hash_file_path, 'r', encoding='utf-8') as f:
            saved_hashes = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"错误: 无法读取或解析哈希文件 {hash_file_path}: {e}")
        return False, None

    current_illustrations = {}
    all_match = True

    for volume, files_data in saved_hashes.items():
        volume_path = os.path.join(download_dir, volume)
        if not os.path.isdir(volume_path):
            all_match = False
            break

        current_illustrations[volume] = []
        for filename, filehash in files_data.items():
            filepath = os.path.join(volume_path, filename)
            calculated_hash = get_file_sha256(filepath)
            if calculated_hash is None or calculated_hash != filehash:
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
    print("\n" + "=" * 15 + " 开始创建EPUB文件" + "=" * 15)
    safe_book_title = re.sub(r'[\\/*?:"<>|]', '', book_title)
    epub_file_path = os.path.join(output_dir, f"{safe_book_title}.epub")

    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{safe_book_title}-{int(time.time())}")
    book.set_title(book_title)
    book.set_language('zh')
    book.add_author(metadata.get('author', '未知'))

    cover_page = None
    if cover_path and os.path.exists(cover_path):
        print("正在处理封面...")
        try:
            with open(cover_path, 'rb') as f:
                cover_content = f.read()
            book.set_cover("cover.jpg", cover_content)
            cover_page = epub.EpubHtml(title='封面', file_name='cover.xhtml', lang='zh')
            cover_page.content = f'<html><head><title>封面</title><style>body{{margin:0;padding:0;text-align:center;}} img{{max-width:100%;max-height:100vh;object-fit:contain;}}</style></head><body><div><img src="cover.jpg" alt="封面"/></div></body></html>'
            book.add_item(cover_page)
        except Exception as e:
            print(f"警告: 添加封面文件 {cover_path} 失败: {e}")

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

    try:
        with open(txt_file_path, 'r', encoding='gbk', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"错误：读取TXT文件失败: {e}")
        return

    def get_core_volume_name(title):
        core_match = re.match(r'(第[一二三四五六七八九十百]+卷|短篇|SS\d*|特典)', title)
        return core_match.group(0) if core_match else title

    core_illustration_map = {get_core_volume_name(k): v for k, v in
                             illustration_data.items()} if illustration_data else {}

    spine_items = ['nav']
    if cover_page:
        spine_items.append(cover_page)
    final_toc = []

    synopsis_chapter = epub.EpubHtml(title='简介', file_name='synopsis.xhtml', lang='zh')
    synopsis_content = metadata.get("synopsis", "无").replace("\n", "</p><p>").replace("　　", "")
    synopsis_chapter.content = f'<h1>简介</h1><p>{synopsis_content}</p>'
    book.add_item(synopsis_chapter)
    spine_items.append(synopsis_chapter)
    final_toc.append(epub.Link('synopsis.xhtml', '简介', 'synopsis'))

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

    book.toc = tuple(final_toc)
    book.spine = spine_items

    style = 'body { font-family: Times, serif; } h1, h2 { text-align: center; font-weight: bold; } p { text-indent: 2em; margin: 0; padding: 0; line-height: 1.6; } .illustration { text-align: center; margin: 1em 0; page-break-before: always;} img { max-width: 100%; height: auto; }'
    nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style)
    book.add_item(nav_css)

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    print("正在写入EPUB文件...")
    try:
        epub.write_epub(epub_file_path, book, {})
        print(f"EPUB文件已成功创建: {epub_file_path}")
    except Exception as e:
        print(f"错误: 创建EPUB文件失败: {e}")


def scrape_metadata(driver):
    metadata = {'author': '未知', 'synopsis': '无', 'cover_url': None}
    print("正在抓取小说元数据（作者、简介、封面）...")

    try:
        author_element = driver.find_element(By.XPATH, "//td[contains(text(), '小说作者：')]")
        metadata['author'] = author_element.text.replace('小说作者：', '').strip()
    except NoSuchElementException:
        print("警告：未找到作者信息。")

    try:
        synopsis_element = driver.find_element(By.XPATH,
                                               "//span[contains(text(), '内容简介：')]/following-sibling::span[1]")
        metadata['synopsis'] = synopsis_element.text.strip()
    except NoSuchElementException:
        print("警告：未找到简介信息。")

    try:
        cover_image_element = driver.find_element(By.XPATH, "//td[@width='20%']//img")
        metadata['cover_url'] = cover_image_element.get_attribute('src')
        print(f"成功获取封面URL: {metadata['cover_url']}")
    except NoSuchElementException:
        print("警告：未找到封面图片。")

    print(f"作者: {metadata['author']}")
    return metadata


def download_image(url, save_path, referer_url):
    try:
        proxies = {"http": None, "https": None}
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': referer_url}
        response = requests.get(url, headers=headers, proxies=proxies, timeout=30)
        if response.ok:
            with open(save_path, 'wb') as f:
                f.write(response.content)

            file_hash = get_file_sha256(save_path)
            if file_hash:
                print(f"插图下载并哈希校验成功: {os.path.basename(save_path)}")
                return save_path, file_hash
            else:
                print(f"警告: 插图 {os.path.basename(save_path)} 下载成功但哈希校验失败。")
                os.remove(save_path)  # 删除不完整或损坏的文件
                return None, None
        else:
            print(f"警告: 下载插图 {os.path.basename(save_path)} 失败，状态码: {response.status_code}")
            return None, None
    except Exception as e:
        print(f"下载插图 {os.path.basename(save_path)} 时发生错误: {e}")
        return None, None


def download_illustrations(driver, book_title):
    print("\n" + "-" * 15 + " 开始处理插图（精确模式）" + "-" * 15)
    volume_images_map = {}
    successful_downloads = []

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
                        if "插图" in link.text:
                            volume_to_illustration_map[current_volume_name] = link.get_attribute('href')
                            break
        if not volume_to_illustration_map:
            print("未发现插图链接。");
            return {}

        download_tasks = []
        for volume_name, url in volume_to_illustration_map.items():
            delay = random.uniform(1.5, 3.5)
            time.sleep(delay)
            driver.execute_script(f"window.open('{url}');")
            driver.switch_to.window(driver.window_handles[-1])
            try:
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "imagecontent")))
                image_elements = driver.find_elements(By.CLASS_NAME, "imagecontent")
                image_srcs = [img.get_attribute('src') for img in image_elements if img.get_attribute('src')]
                if image_srcs:
                    safe_book_title = re.sub(r'[\\/*?:"<>|]', '', book_title)
                    illust_dir = os.path.join("download", safe_book_title, volume_name)
                    os.makedirs(illust_dir, exist_ok=True)
                    current_page_url = driver.current_url
                    for j, img_src in enumerate(image_srcs):
                        file_name = os.path.basename(urlparse(img_src).path) or f"{j + 1:02d}.jpg"
                        save_path = os.path.join(illust_dir, file_name)
                        download_tasks.append((img_src, save_path, current_page_url))
            finally:
                driver.close()
                driver.switch_to.window(novel_index_window)

        if not download_tasks:
            return {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:  # 适当增加并发，但避免过高
            results = list(executor.map(lambda p: download_image(*p), download_tasks))

        for original_task, (path, file_hash) in zip(download_tasks, results):
            if path and file_hash:  # 只有下载成功且哈希校验通过的才算成功
                # 从 original_task 中提取 volume_name，这需要修改 original_task 的结构
                # 或者更简单的方式是重新构建 volume_images_map
                volume_name_from_path = os.path.basename(os.path.dirname(path))
                if volume_name_from_path not in volume_images_map:
                    volume_images_map[volume_name_from_path] = []
                volume_images_map[volume_name_from_path].append(path)
                successful_downloads.append(path)  # 收集所有成功下载的路径


    except Exception as e:
        print(f"插图处理出错: {e}");
        return {}

    return volume_images_map


def find_and_download_novel(driver, book_title):
    print("\n" + "=" * 20 + f" 开始处理书籍: {book_title} " + "=" * 20)

    safe_book_title = re.sub(r'[\\/*?:"<>|]', '', book_title)
    download_dir = os.path.join("download", safe_book_title)
    hash_file = os.path.join(download_dir, ".hash")
    os.makedirs(download_dir, exist_ok=True)

    illustrations_ok, illustration_data = check_hashes(hash_file, download_dir)

    original_window = driver.current_window_handle
    if not search_for_novel(driver, book_title):
        print(f"错误: 未能找到或搜索小说《{book_title}》。")
        return

    cover_image_path = None

    try:
        WebDriverWait(driver, 10).until(EC.number_of_windows_to_be(2))
        novel_window = [window for window in driver.window_handles if window != original_window][0]
        driver.switch_to.window(novel_window)

        metadata = scrape_metadata(driver)

        if metadata.get('cover_url'):
            cover_image_path = os.path.join(download_dir, 'cover.jpg')
            print(f"正在下载封面至: {cover_image_path}")
            try:
                response = requests.get(metadata['cover_url'], timeout=30)
                if response.ok:
                    with open(cover_image_path, 'wb') as f:
                        f.write(response.content)
                    if not get_file_sha256(cover_image_path):
                        print("警告: 封面下载成功但哈希校验失败，可能文件损坏。")
                        os.remove(cover_image_path)
                        cover_image_path = None
                    else:
                        print("封面下载并哈希校验成功。")
                else:
                    print(f"警告: 封面下载失败，状态码: {response.status_code}")
                    cover_image_path = None
            except Exception as e:
                print(f"封面下载失败: {e}")
                cover_image_path = None

        parsed_url = urlparse(driver.current_url)
        novel_id = parsed_url.path.strip('/').split('/')[1].replace('.htm', '')

        if not illustrations_ok:
            new_illustration_data = download_illustrations(driver, book_title)
            if new_illustration_data:
                save_hashes(hash_file, new_illustration_data)
                illustration_data = new_illustration_data  # 更新为最新的插图数据
            else:
                print("未下载任何插图。")
                illustration_data = {}  # 确保它是字典类型

    except Exception as e:
        print(f"处理小说详情页或插图时发生严重错误: {e}")
        return
    finally:
        while len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            driver.close()
        driver.switch_to.window(original_window)

    txt_save_path = os.path.join(download_dir, f"{safe_book_title}.txt")

    txt_success = False
    if os.path.exists(txt_save_path):
        # 简单检查txt文件是否为空
        if os.path.getsize(txt_save_path) > 100:
            print(f"TXT文件 {txt_save_path} 已存在且非空，跳过下载。")
            txt_success = True
        else:
            print(f"TXT文件 {txt_save_path} 存在但为空或过小，将尝试重新下载。")
            os.remove(txt_save_path)

    if not txt_success:
        referer = f"https://www.wenku8.net/modules/article/packshow.php?id={novel_id}&type=txtfull"
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        txt_success = download_txt(novel_id, 1, txt_save_path, cookies, referer)
        if not txt_success:
            txt_success = download_txt(novel_id, 2, txt_save_path, cookies, referer)

    if not txt_success:
        print(f"《{book_title}》TXT 文件下载失败，无法创建EPUB。")
        return

    create_epub(book_title, metadata, txt_save_path, illustration_data, download_dir, cover_path=cover_image_path)


def login_wenku8(driver, username, password):
    print("正在尝试登录...")
    try:
        u = driver.find_element(By.NAME, "username")
        p = driver.find_element(By.NAME, "password")
        b = driver.find_element(By.NAME, "submit")
        u.send_keys(username)
        p.send_keys(password)
        b.click()
        time.sleep(10)
        if "欢迎您" in driver.page_source:
            print("登录成功！");
            return True
        else:
            print("登录失败，请检查用户名和密码。");
            return False
    except Exception as e:
        print(f"登录时出错: {e}");
        return False


def search_for_novel(driver, book_title):
    try:
        s = driver.find_element(By.ID, "searchkey")
        b = driver.find_element(By.NAME, "Submit")
        s.clear()
        s.send_keys(book_title)
        b.click()
        WebDriverWait(driver, 10).until(EC.url_contains("search.php"))
        return True
    except Exception as e:
        print(f"搜索时出错: {e}");
        return False


def download_txt(novel_id, node, save_path, cookies, referer_url):
    url = f"https://dl.wenku8.com/down.php?type=txt&node={node}&id={novel_id}"
    print(f"正在从 Node={node} 下载TXT...")
    try:
        proxies = {"http": None, "https": None}
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': referer_url}
        response = requests.get(url, cookies=cookies, timeout=60, proxies=proxies, headers=headers)
        if response.ok and len(response.content) > 100:
            with open(save_path, 'wb') as f:
                f.write(response.content)
            print(f"TXT文件下载成功: {save_path}")
            return True
        else:
            print(f"TXT文件下载失败或内容为空。状态码: {response.status_code}, 内容大小: {len(response.content)}")
            return False
    except Exception as e:
        print(f"下载TXT文件时发生错误: {e}")
        return False


def main():
    if not hasattr(config, 'NOVEL_LIST') or not config.NOVEL_LIST:
        print("错误：config.py 中未找到或未定义 NOVEL_LIST。")
        return
    if not hasattr(config, 'USERNAME') or not hasattr(config, 'PASSWORD') or not config.USERNAME or not config.PASSWORD:
        print("错误：config.py 中未设置 USERNAME 或 PASSWORD。")
        return

    print("正在初始化浏览器驱动...")
    service = Service(ChromeDriverManager().install())
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")  # 无头模式
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=service, options=options)
    base_url = "https://www.wenku8.net"

    try:
        driver.get(base_url)
        if login_wenku8(driver, config.USERNAME, config.PASSWORD):
            print("\n开始自动化处理小说列表...")
            for book_title in config.NOVEL_LIST:
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


if __name__ == "__main__":
    main()
