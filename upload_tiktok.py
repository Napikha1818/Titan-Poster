#!/usr/bin/env python3
"""Standalone TikTok upload script — called by scheduler subprocess."""
import sys
import json
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()
from poster.tiktok import upload_video

if len(sys.argv) < 3:
    print(json.dumps({"success": False, "error": "Usage: upload_tiktok.py <video_path> <caption>"}))
    sys.exit(1)

video_path = sys.argv[1]
caption = sys.argv[2]
result = upload_video(video_path, caption)
print(json.dumps(result))
