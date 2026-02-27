#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# scripts/weirdhost_renew.py

import os
import time
import asyncio
import aiohttp
import base64
import random
import re
import subprocess
import json
from datetime import datetime, timedelta
from urllib.parse import unquote

from seleniumbase import SB

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

BASE_URL = "https://hub.weirdhost.xyz/server/"
DOMAIN = "hub.weirdhost.xyz"

RENEW_THRESHOLD_DAYS = int(os.environ.get("RENEW_THRESHOLD_DAYS", "2"))


def mask_sensitive(text, show_chars=3):
    if not text:
        return "***"
    text = str(text)
    if len(text) <= show_chars * 2:
        return "*" * len(text)
    return text[:show_chars] + "*" * (len(text) - show_chars * 2) + text[-show_chars:]


def mask_email(email):
    if not email or "@" not in email:
        return mask_sensitive(email)
    local, domain = email.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def mask_server_id(server_id):
    if not server_id:
        return "***"
    if len(server_id) <= 4:
        return "*" * len(server_id)
    return server_id[:2] + "*" * (len(server_id) - 4) + server_id[-2:]


def mask_url(url):
    if not url:
        return "***"
    if "/server/" in url:
        parts = url.split("/server/")
        if len(parts) == 2:
            return parts[0] + "/server/" + mask_server_id(parts[1])
    return url


def parse_accounts():
    """解析 ACCOUNTS 环境变量"""
    accounts_str = os.environ.get("ACCOUNTS", "").strip()
    
    if not accounts_str:
        print("\n" + "=" * 60)
        print("❌ 错误: WEIRDHOST_ACCOUNTS 环境变量未设置")
        print("=" * 60)
        return []
    
    try:
        accounts = json.loads(accounts_str)
        if not isinstance(accounts, list):
            print("\n" + "=" * 60)
            print("❌ 错误: ACCOUNTS 格式错误，应为 JSON 数组")
            print("=" * 60)
            return []
        
        valid_accounts = []
        for i, acc in enumerate(accounts):
            if not isinstance(acc, dict):
                continue
            
            missing = []
            if not acc.get("id"):
                missing.append("id")
            if not acc.get("cookie_env"):
                missing.append("cookie_env")
            
            if missing:
                print(f"[!] 账号 {i+1} 缺少必要字段: {', '.join(missing)}")
                continue
            
            valid_accounts.append(acc)
        
        if not valid_accounts:
            return []
        
        print(f"[+] 解析到 {len(valid_accounts)} 个有效账号配置")
        return valid_accounts
        
    except json.JSONDecodeError as e:
        print("\n" + "=" * 60)
        print("❌ 错误: ACCOUNTS JSON 解析失败")
        print("=" * 60)
        return []


def parse_weirdhost_cookie(cookie_str):
    if not cookie_str:
        return (None, None)
    cookie_str = cookie_str.strip()
    if "=" in cookie_str:
        parts = cookie_str.split("=", 1)
        if len(parts) == 2:
            return (parts[0].strip(), unquote(parts[1].strip()))
    return (None, None)


def build_server_url(server_id):
    if not server_id:
        return None
    server_id = server_id.strip()
    return server_id if server_id.startswith("http") else f"{BASE_URL}{server_id}"


def calculate_remaining_time(expiry_str):
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                diff = expiry_dt - datetime.now()
                if diff.total_seconds() < 0:
                    return "⚠️ 已过期"
                days = diff.days
                hours = diff.seconds // 3600
                minutes = (diff.seconds % 3600) // 60
                parts = []
                if days > 0:
                    parts.append(f"{days}天")
                if hours > 0:
                    parts.append(f"{hours}小时")
                if minutes
