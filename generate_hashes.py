import os
import hashlib
import json

# --- 配置区 ---
# 确认你的下载目录名是否为 "download"
DOWNLOAD_ROOT = 'download'
# 支持的图片文件扩展名
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp')


def get_file_sha256(filepath):
    """计算文件的SHA256哈希值 (与主程序完全相同)"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            # 读取文件块以处理大文件
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except IOError as e:
        print(f"  错误: 无法读取文件 {filepath}: {e}")
        return None


def generate_hashes_for_existing_downloads():
    """主函数：遍历下载目录并生成.hash文件"""
    if not os.path.isdir(DOWNLOAD_ROOT):
        print(f"错误: 未找到下载目录 '{DOWNLOAD_ROOT}'。请确保此脚本与该目录在同一级别。")
        return

    print(f"开始扫描 '{DOWNLOAD_ROOT}' 目录下的书籍...")

    # 遍历所有书籍文件夹
    for book_name in os.listdir(DOWNLOAD_ROOT):
        book_path = os.path.join(DOWNLOAD_ROOT, book_name)
        if not os.path.isdir(book_path):
            continue

        print(f"\n正在处理书籍: {book_name}")
        book_hashes = {}
        has_illustrations = False

        # 遍历书籍目录下的所有子目录（即卷文件夹）
        for item_name in os.listdir(book_path):
            item_path = os.path.join(book_path, item_name)
            # 假设所有子目录都是插图卷
            if os.path.isdir(item_path):
                volume_name = item_name
                book_hashes[volume_name] = {}
                image_found_in_volume = False

                # 遍历卷文件夹中的所有文件
                for filename in os.listdir(item_path):
                    if filename.lower().endswith(IMAGE_EXTENSIONS):
                        image_path = os.path.join(item_path, filename)

                        # 计算哈希值
                        file_hash = get_file_sha256(image_path)
                        if file_hash:
                            book_hashes[volume_name][filename] = file_hash
                            image_found_in_volume = True

                if image_found_in_volume:
                    has_illustrations = True
                    print(f"  - 已处理卷: {volume_name}")

        # 如果找到了插图，就创建.hash文件
        if has_illustrations:
            hash_file_path = os.path.join(book_path, '.hash')
            try:
                with open(hash_file_path, 'w', encoding='utf-8') as f:
                    json.dump(book_hashes, f, ensure_ascii=False, indent=4)
                print(f"成功为《{book_name}》创建 .hash 文件。")
            except IOError as e:
                print(f"错误: 无法写入 .hash 文件到 {book_path}: {e}")
        else:
            print(f"《{book_name}》中未找到插图文件夹，跳过。")

    print("\n所有现有书籍的哈希文件生成完毕！")


if __name__ == "__main__":
    generate_hashes_for_existing_downloads()
