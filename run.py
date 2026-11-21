import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = ["fastapi", "uvicorn", "httpx", "dotenv", "multipart", "yt_dlp", "pypdf", "docx"]


def missing_modules() -> list[str]:
    missing = []
    for module in REQUIRED:
        try:
            __import__(module)
        except Exception:
            missing.append(module)
    return missing


def install_dependencies() -> None:
    print("检测到缺少依赖，正在安装 requirements.txt ...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
    )
    if result.returncode != 0:
        print("依赖安装失败，请手动执行： pip install -r requirements.txt")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动知萃学习知识点提炼站")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if missing_modules():
        install_dependencies()

    env_file = ROOT / ".env"
    if not env_file.exists():
        example = ROOT / ".env.example"
        if example.exists():
            shutil.copy(example, env_file)
            print(f"已生成 {env_file}，请填入大模型 API Key 后重启；未填写将以演示模式运行。")

    from app.config import HOST, PORT
    import uvicorn

    host = args.host or HOST
    port = args.port or PORT
    print(f"知萃已启动： http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=args.reload)


if __name__ == "__main__":
    main()