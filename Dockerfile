# SmartClass AI Tracker & Reviewer — container packaging
#
# This image packages the headless, cross-platform parts of the project
# (AI review, data export/statistics, and the OPEA integration bridge).
# The WeChat PC client automation layer (wxauto4 / pywin32 / pywinauto /
# pyautogui) is Windows-only and runs on the Windows host, so it is
# intentionally excluded from this Linux image.
#
# OPEA (Open Platform for Enterprise AI) reference: https://opea.dev/
# The default command runs the OPEA connectivity demo so the image has a
# runnable, self-contained entrypoint.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install only the cross-platform dependencies. Windows-only automation
# packages (wxauto4, pywin32, uiautomation, pywinauto, pyautogui, xlwings)
# are omitted — they belong to the Windows host, not this image.
RUN pip install --no-cache-dir \
    pandas==3.0.3 \
    openpyxl==3.1.5 \
    pillow==12.3.0 \
    websocket-client==1.9.0 \
    dashscope==1.26.3 \
    python-dotenv==1.2.2 \
    requests==2.34.2 \
    openai==2.45.0

COPY . /app

# OPEA GenAIComps endpoints (override at runtime via env vars).
ENV OPEA_EMBEDDING_URL=http://localhost:6000/v1/embeddings \
    OPEA_LLM_URL=http://localhost:9000/v1/chat/completions \
    OPEA_RERANK_URL=http://localhost:8000/v1/reranking \
    OPEA_RETRIEVER_URL=http://localhost:7000/v1/retrieval

CMD ["python", "opea_demo.py"]
