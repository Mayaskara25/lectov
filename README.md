# lectov 
Lecture to video

### Installing dependencies and running 

Installation:
```sh
# ensure you have uv installed
uv venv
source .venv/bin/activate
uv add flask litellm litellm[openai] pypdf python-dotenv manim
```

Running:
```sh
# Server: 
uv run main.py

# Request a video:
curl -X POST http://localhost:6001/generate \                      
 -F "file=@file.txt"    
```

### Roadmap
[ ] Handwritten images support (No OCR)
[ ] Web UI
