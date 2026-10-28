"""
启动Web界面：python web_ui.py
访问 http://localhost:8000 即可使用
"""
import subprocess
import sys
import os
import webbrowser
from threading import Timer

# 自动打开浏览器
def open_browser():
    webbrowser.open("http://localhost:8080")

if __name__ == "__main__":
    Timer(1.5, open_browser).start()
    # 用uvicorn启动，带静态文件
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"])
