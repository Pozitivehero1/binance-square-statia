from config import settings
from youtube_upload import get_credentials

if __name__ == "__main__":
    get_credentials(settings, interactive=True)
    print(f"OAuth token saved to: {settings.youtube_token_file}")
    print("You can now enable AUTO_UPLOAD=1")
