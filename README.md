# lectov 
Lecture to video pipeline. 

Teachers and students alike can leverage this pipeline to generate animations
from class notes / lecture notes. It helps visualise a concept which is a great addition to
teaching as well as learning. The resulting video is purely created out of python code using
the manim (https://www.manim.community/) animation library. This allows quick and deterministic
changes to the video if the generated video has small bugs.

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
