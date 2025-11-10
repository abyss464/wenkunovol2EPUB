# 轻小说文库小说下载转EPUB工具

一个简易的Python脚本，用于从轻小说文库(https://www.wenku8.net)下载小说（含文本、插图和封面）并生成带目录、封面和插图的EPUB文件，支持批量下载和插图增量更新。

## 1. 安装依赖

在项目根目录运行命令安装：

```bash
pip install -r requirements.txt
```
安装Chrome浏览器

## 2. 配置 `config.py`

在脚本同级目录下创建 `config.py`，填入你的Wenku8账号和想下载的小说列表：

```python
# config.py

USERNAME = "你的轻小说文库用户名"
PASSWORD = "你的密码"

NOVEL_LIST = [
    "某科学的超电磁炮",
    "关于我转生变成史莱姆这档事",
    # ... 一定要精准匹配
]
```

## 3. 运行脚本

```bash
python novolmanager.py
```

合成的EPUB文件和插图会保存在 `download/小说标题/` 目录下。

---
