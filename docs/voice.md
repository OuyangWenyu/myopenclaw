# Hermes 语音（DeepSeek TTS/STT）

Hermes 的语音输入/输出默认走 DeepSeek 的语音接口：

- **STT**：飞书语音消息 → 下载 → `POST /v1/tools/asr` → 转写文本
- **TTS**：回复文本 → `POST /v1/tools/tts` → wav → Hermes 用 ffmpeg 转 ogg/opus → 飞书语音气泡

能力只实现一次，随 `myopenclaw/hermes` 镜像发布在 `/opt/hermes-voice/`；配置由 `scripts/start.sh` 和 `scripts/bind_agent.py` 在创建/启动时注入。默认 profile、`profiles/*`、以及扫码新增 agent 都走同一套脚本，没有按 profile 写死的分支。

## 前置条件

- `DEEPSEEK_API_KEY`：仓库根 `.env`（compose 注入容器）或 bind agent 自己的 `.env`。语音与模型共用这一个 key。
- `ffmpeg`：Hermes 镜像自带；没有它 TTS 只能产出 wav，飞书侧无法转语音气泡。
- 网络可达 `https://api.deepseek.com`。

没有 `DEEPSEEK_API_KEY` 时，`start.sh` 只安装 provider 定义、不切换 provider；运行时会以清晰的 “DEEPSEEK_API_KEY is not set” 失败，文字回复不受影响。

## 默认行为

```yaml
tts:
  provider: deepseek
  providers:
    deepseek:
      type: command
      command: "python3 /opt/hermes-voice/ds_voice.py tts --text-file {input_path} --out {output_path} --format {format} --voice {voice}"
      output_format: wav
      voice: stella
      voice_compatible: true
      max_text_length: 4000
      timeout: 180
      env_passthrough:
      - DEEPSEEK_API_KEY

stt:
  enabled: true
  provider: deepseek
  providers:
    deepseek:
      type: command
      command: "python3 /opt/hermes-voice/ds_voice.py asr --in {input_path} --out {output_path}"
      format: txt
      timeout: 300
      env_passthrough:
      - DEEPSEEK_API_KEY
```

- **入站语音**：全部走 DeepSeek ASR（此前默认的本地 whisper 在本镜像里不可用）。
- **出站语音**：`voice.auto_tts: true` 时，只有“用户发来语音消息”的回合会自动回语音；普通文字消息仍回文字。需要文字消息也回语音时，让模型调用 `text_to_speech` 工具。
- 所有 Hermes profile（含新建 bind agent）默认 `voice.auto_tts: true`；需要临时关闭时在会话里 `/voice off`，`/voice on` 重新打开。

## 音色

`tts.providers.deepseek.voice` 可选：

- `stella`：女声，默认
- `tide`：男声

改完重启对应 Hermes 容器生效。

## 成本与并发

官方人民币价（限时 5 折）：

| 项目 | 刊例价 | 折后 |
| --- | --- | --- |
| TTS | 3 元/万计费字符 | 1.5 元/万计费字符 |
| ASR | 0.78 元/小时 | 0.39 元/小时 |

- TTS 按“计费字符”算：汉字 2 个字符，英文/数字/空格等 1 个字符。
- 一条 300 汉字的回复 ≈ 600 计费字符 ≈ **0.09 元**；1000 条/月 ≈ **90 元**。
- ASR 按音频时长计费，10 秒 ≈ **0.0011 元**。
- 每个 DeepSeek 账号最多 50 个并发请求，所有 agent 共享；超过会返回 429，脚本会自动重试 3 次。

## 隐私

ASR 会把语音上传到 DeepSeek，TTS 会把回复文本上传到 DeepSeek。不能接受上云时，把 `stt.provider` 改回 `local` 并先修好本地 whisper；或设 `stt.enabled: false` 关闭语音输入。

## 限制与错误

DeepSeek 接口限制：

- TTS：单次 ≤10,000 计费字符，请求体 ≤64 KiB，单次最多 30 分钟音频。
- ASR：≤8 MiB、≤120 秒；只接受 16-bit PCM WAV（8k–48k）或 Ogg Opus；不支持流式。
- 400 内容审核/空文本；429 并发；503 繁忙；504 超时。脚本对 429/503/504/408 重试 3 次。

`ds_voice.py` 退出码：

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 参数/文本超限 |
| 3 | 未设置 `DEEPSEEK_API_KEY` |
| 4 | 上游 HTTP/网络失败 |
| 5 | 音频不支持/超限 |
| 6 | 空转写 |
| 7 | ffmpeg 缺失/转码失败 |

## 飞书播放行为

飞书客户端的语音消息是**点击播放**，没有“收到语音自动播放”的开关；设置里只有“语音消息自动转为文字”（桌面端/移动端：头像 → 设置 → 效率）。这是飞书客户端行为，机器人无法强制自动播放。

如果语音回复没有出现、只回了文字，先确认镜像里的安全写根：

```text
HERMES_WRITE_SAFE_ROOT=/opt/data:/tmp/hermes_voice
```

Hermes 的 auto-TTS 固定写 `/tmp/hermes_voice`；安全写根不包含它时，TTS 工具会返回 “protected credential or system path”，网关会静默退回纯文字。本镜像已在 Dockerfile 里设置该值。

## 手动命令

```bash
# 合成
docker compose exec hermes python3 /opt/hermes-voice/ds_voice.py \
  tts --text "你好，这是测试。" --out /tmp/tts.wav --format wav --voice stella

# 转写（输出到 stdout）
docker compose exec hermes python3 /opt/hermes-voice/ds_voice.py \
  asr --in /tmp/voice.wav --stdout

# 检查某份 config 是否已安装能力
docker compose exec hermes python3 /opt/hermes-voice/voice_config.py \
  --check /opt/data/config.yaml
```

## 注入与迁移

`scripts/start.sh` 会在启动前对以下文件运行 `voice_config.py --ensure`：

- `~/.hermes/config.yaml`
- `~/.hermes/profiles/*/config.yaml`
- `~/.hermes-agents/*/profiles/*/config.yaml`

`scripts/bind_agent.py` 在新建 agent 时写入同样配置。注入器：

- 只编辑 `tts` / `stt` / `voice` 段，保留注释、键序、缩进、换行风格；
- 只把 `edge` / `local` / 空切换为 `deepseek`，显式选择（`openai`、`elevenlabs` 等）原样保留；
- `stt.enabled: false` 时整段跳过；
- 改动前生成 `config.yaml.bak.voice-<时间戳>`；
- 可先 `--dry-run` 看 diff，再 `--ensure` 落盘。

## 社区版说明

- 运行时路径固定为镜像内 `/opt/hermes-voice/`，不依赖宿主机目录；
- `ds_voice.py` 与 `voice_config.py` 只用 Python 标准库，宿主和容器都能跑；
- 不引入第二套密钥名：只读 `DEEPSEEK_API_KEY`；
- 不修改 Hermes 上游代码，使用 Hermes 原生的 `tts.providers.<name>: type: command` / `stt.providers.<name>: type: command` 契约。
