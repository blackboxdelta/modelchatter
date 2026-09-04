# modelchatter

Watch two local LLMs talk to each other, in your browser, at a speed you can actually read.

Point it at two models running in [Ollama](https://ollama.com), give them an opening line, and watch the conversation stream in live — with token counts and tokens/sec per turn.

![status](https://img.shields.io/badge/dependencies-none-brightgreen) ![python](https://img.shields.io/badge/python-3.8%2B-blue) ![license](https://img.shields.io/badge/license-MIT-yellow)

## Why

Small local models generate at 150–300 tokens/sec. If you pipe that straight to the DOM, each reply lands as an instant wall of text and the page scroll-jumps on every token — technically "streaming," practically unreadable.

modelchatter decouples **display speed** from **generation speed**. Tokens buffer as they arrive and play back at a reading pace you control, so you can watch a conversation unfold instead of watching it teleport.

When you *do* want the firehose, hit **⚡ MAX**.

## Requirements

- Python 3.8+ (standard library only — no `pip install`)
- [Ollama](https://ollama.com) running locally
- At least two chat models pulled

## Quick start

```bash
# pull a couple of small, fast models
ollama pull qwen2.5:0.5b     # 379 MB
ollama pull llama3.2:1b      # 1.2 GB

# run it
python3 server.py
```

Then open **http://127.0.0.1:8777**

Pick two models, choose an opening line, press **Start**.

```bash
python3 server.py --port 9000    # custom port
```

## Controls

| Control | What it does |
|---|---|
| **Playback** | Reading pace, 60–700 wpm |
| **⚡ MAX** | No throttle — renders as fast as the models generate |
| **Pause / Resume** | Freezes playback; generation keeps running |
| **Stop** | Ends the conversation |
| **Rounds** | How many back-and-forth exchanges |
| **Temp** | Sampling temperature |
| **Max tok** | Token cap per reply |

MAX can be toggled mid-sentence — the buffered backlog dumps instantly.

## How it works

```
Ollama (NDJSON stream)
   -> server.py         worker thread per turn, feeds a queue.Queue
   -> SSE               /api/converse emits typed events
   -> browser           EventSource receives tokens
   -> typewriter queue  drains at your chosen pace
   -> DOM
```

**Two separate histories.** Each model gets its own message list and its own system persona naming itself and the other model. Model A's reply becomes Model B's `user` message, and vice versa — neither model ever sees a shared transcript, so each genuinely believes it's talking to someone else.

**Turn queue.** Because generation outruns playback, the server can finish turn *N+1* while turn *N* is still typing out. Whole turns buffer and play strictly in order.

**Contained scrolling.** The transcript scrolls in its own box; the page never moves. Auto-scroll only follows along if you're already near the bottom — scroll up and it stops chasing you.

### Endpoints

| Route | Purpose |
|---|---|
| `GET /` | The UI |
| `GET /api/health` | Ollama reachability |
| `GET /api/models` | Installed chat models (embeddings filtered out) |
| `GET /api/converse` | SSE conversation stream |

SSE event types: `seed`, `turn_start`, `token`, `turn_end`, `done`, `stopped`, `error`.

## Notes

- Ollama unloads models after ~5 minutes idle, so the first reply after a break includes a reload pause. Pin them with `OLLAMA_KEEP_ALIVE=-1`.
- The system persona asks for 2–4 sentence replies with no markdown or lists. Without that constraint, small models tend to produce essay dumps and asterisk stage-directions.
- `<think>...</think>` blocks are stripped from reasoning models' output.
- Everything is local. The only host the app talks to is `127.0.0.1`.

## Files

```
server.py     stdlib HTTP server, Ollama relay, conversation loop
index.html    UI, typewriter engine, turn queue
```

## License

MIT — see [LICENSE](LICENSE).
