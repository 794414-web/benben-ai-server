"""
AI助手代理Mock服务
用途：拦截AI助手发给大模型的所有请求，分析工具定义和控制API结构
用法：python proxy_mock.py
然后在AI助手配置中把"服务地址"改为 http://127.0.0.1:9999/compatible-mode/v1
"""

import json
import os
import sys
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# 配置
HOST = "0.0.0.0"
PORT = 9999
LOG_DIR = "mock_logs"

# 确保日志目录存在
os.makedirs(LOG_DIR, exist_ok=True)


def save_log(filename, data):
    """保存请求/响应到日志文件"""
    filepath = os.path.join(LOG_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_fake_response(request_data, path):
    """构建一个假的大模型响应，让AI助手以为调用成功"""

    # 如果请求包含 tools 定义，说明AI助手在要求大模型调用工具
    tools_in_request = request_data.get("tools", [])
    messages = request_data.get("messages", [])

    # 提取用户最后一条消息内容
    last_user_content = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # 多模态消息，提取文本部分
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        last_user_content += part.get("text", "")
            elif isinstance(content, str):
                last_user_content = content
            break

    # 如果有工具定义，模拟一个工具调用响应
    if tools_in_request and last_user_content:
        # 根据关键词匹配可能的工具调用
        tool_call_name = None
        tool_call_args = {}

        content_lower = last_user_content.lower()

        # 简单关键词匹配
        keyword_map = {
            "开灯": ("turn_on_light", {"room": "客厅"}),
            "关灯": ("turn_off_light", {"room": "客厅"}),
            "亮": ("turn_on_light", {"room": "客厅"}),
            "关": ("turn_off_light", {"room": "客厅"}),
            "温度": ("get_temperature", {}),
            "湿度": ("get_humidity", {}),
            "天气": ("get_weather", {}),
            "音量": ("set_volume", {"level": 50}),
            "播放": ("play_music", {}),
            "停止": ("stop_media", {}),
            "拍照": ("take_photo", {}),
            "录像": ("start_recording", {}),
            "打开": ("turn_on_device", {}),
            "关闭": ("turn_off_device", {}),
        }

        for keyword, (name, args) in keyword_map.items():
            if keyword in last_user_content:
                tool_call_name = name
                tool_call_args = args
                break

        if tool_call_name:
            # 返回工具调用格式的响应
            response = {
                "id": f"mock-{int(time.time())}",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"call_{int(time.time())}",
                                    "type": "function",
                                    "function": {
                                        "name": tool_call_name,
                                        "arguments": json.dumps(tool_call_args, ensure_ascii=False),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "created": int(time.time()),
                "model": request_data.get("model", "qwen"),
                "object": "chat.completion",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            }
        else:
            # 没有匹配的关键词，返回普通文本响应
            response = {
                "id": f"mock-{int(time.time())}",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"我收到了你的消息：{last_user_content}",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "created": int(time.time()),
                "model": request_data.get("model", "qwen"),
                "object": "chat.completion",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 30,
                    "total_tokens": 130,
                },
            }
    else:
        # 无工具定义，返回普通文本响应
        response = {
            "id": f"mock-{int(time.time())}",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "连接测试成功！这是来自Mock代理的假响应。",
                    },
                    "finish_reason": "stop",
                }
            ],
            "created": int(time.time()),
            "model": request_data.get("model", "qwen"),
            "object": "chat.completion",
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70,
            },
        }

    return response


class MockHandler(BaseHTTPRequestHandler):
    """HTTP请求处理器"""

    def do_POST(self):
        """处理POST请求"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            request_data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            request_data = {"raw_body": body.decode("utf-8", errors="replace")}

        path = self.path
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        # 保存请求日志
        log_filename = f"request_{timestamp}.json"
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "method": "POST",
            "path": path,
            "headers": dict(self.headers),
            "body": request_data,
        }
        save_log(log_filename, log_data)

        # 打印关键信息到控制台
        print(f"\n{'='*60}")
        print(f"[请求] {path}")
        print(f"[日志] 已保存到 {LOG_DIR}/{log_filename}")

        # 检查是否有 tools 定义
        tools = request_data.get("tools", [])
        if tools:
            print(f"\n[!!!] 发现工具定义！共 {len(tools)} 个工具：")
            for i, tool in enumerate(tools):
                func = tool.get("function", {})
                print(f"  工具{i+1}: {func.get('name', 'unknown')}")
                print(f"    描述: {func.get('description', '无')[:100]}")
                params = func.get("parameters", {})
                if params:
                    print(f"    参数: {json.dumps(params, ensure_ascii=False)[:200]}")
                print()

        # 检查 messages 中的 system prompt
        messages = request_data.get("messages", [])
        system_prompt = ""
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_prompt = content
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            system_prompt += part.get("text", "")
                break

        if system_prompt:
            print(f"\n[System Prompt] ({len(system_prompt)}字)")
            print(f"  {system_prompt[:200]}..." if len(system_prompt) > 200 else f"  {system_prompt}")

        # 构建假响应
        response_data = build_fake_response(request_data, path)

        # 发送响应
        response_json = json.dumps(response_data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_json)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response_json)

        # 保存响应日志
        resp_log_filename = f"response_{timestamp}.json"
        save_log(resp_log_filename, response_data)

        print(f"[响应] 已发送假响应，日志: {LOG_DIR}/{resp_log_filename}")
        print(f"{'='*60}\n")

    def do_GET(self):
        """处理GET请求 - 健康检查"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Mock Proxy is running...")

    def do_OPTIONS(self):
        """处理CORS预检请求"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def log_message(self, format, *args):
        """重写日志方法，减少噪音"""
        pass


def main():
    server = HTTPServer((HOST, PORT), MockHandler)

    print(f"""
╔══════════════════════════════════════════════════════════╗
║           AI助手代理Mock服务 v1.0                        ║
╠══════════════════════════════════════════════════════════╣
║  服务地址: http://{HOST}:{PORT}/compatible-mode/v1          ║
║  日志目录: {os.path.abspath(LOG_DIR)}                        ║
║  状态: 运行中...                                         ║
║                                                          ║
║  使用步骤:                                               ║
║  1. 在AI助手配置中，把"服务地址"改为:                    ║
║     http://{HOST}:{PORT}/compatible-mode/v1              ║
║  2. API Key 填任意值 (如: mock-key-123)                  ║
║  3. 点击"测试对话"按钮                                   ║
║  4. 查看控制台输出和日志文件                              ║
║                                                          ║
║  按 Ctrl+C 停止服务                                      ║
╚══════════════════════════════════════════════════════════╝
""")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[停止] 服务已关闭")
        server.shutdown()


if __name__ == "__main__":
    main()
