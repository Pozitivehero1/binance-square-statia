import subprocess
import os

def publish(text, title, cover_path=None):
    """Публикует статью на Binance Square через официальный навык."""
    skill_dir = find_skill_dir()
    if not skill_dir:
        print("[PUBLISH] Skill not found.")
        return False

    api_key = os.getenv("SQUARE_API") or os.getenv("BINANCE_SQUARE_OPENAPI_KEY")
    if api_key:
        api_key = api_key.strip()
    else:
        print("[PUBLISH] No API key.")
        return False

    env = os.environ.copy()
    env["BINANCE_SQUARE_OPENAPI_KEY"] = api_key

    script = os.path.join(skill_dir, "scripts", "post-image.mjs")
    
    # Если есть обложка — делаем абсолютный путь
    if cover_path and os.path.exists(cover_path):
        abs_cover_path = os.path.abspath(cover_path)
        cmd = ["node", script, "--text", text, "--title", title, "--cover", abs_cover_path]
        print(f"[PUBLISH] Publishing article with cover: {' '.join(cmd)}")
    else:
        cmd = ["node", script, "--text", text, "--title", title]
        print(f"[PUBLISH] Publishing article without cover: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=skill_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=60
        )
        print("[PUBLISH] STDOUT:", result.stdout)
        if result.stderr:
            print("[PUBLISH] STDERR:", result.stderr)
        
        if "Success!" in result.stdout or "Content ID" in result.stdout:
            return True
        return result.returncode == 0
    except Exception as e:
        print(f"[PUBLISH] ERROR: {e}")
        return False


def find_skill_dir():
    """Ищет директорию установленного навыка square-post."""
    base_paths = [
        os.getenv("GITHUB_WORKSPACE", "."),
        ".",
    ]
    for base in base_paths:
        candidate = os.path.join(base, ".agents", "skills", "square-post")
        if os.path.exists(os.path.join(candidate, "scripts", "post-image.mjs")):
            return candidate

    alt_paths = [
        os.path.expanduser("~/.agents/skills/square-post"),
        "./node_modules/@binance/square-post",
        "./skills/binance/square-post",
    ]
    for path in alt_paths:
        if os.path.exists(os.path.join(path, "scripts", "post-image.mjs")):
            return path
    return None
