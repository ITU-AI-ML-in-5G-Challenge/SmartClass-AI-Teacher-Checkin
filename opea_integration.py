"""
OPEA (Open Platform for Enterprise AI) HTTP client.

OPEA — https://opea.dev/ — is an open, Linux Foundation–hosted project
(initiated with Intel) that provides cloud-native, composable GenAI
microservices and enterprise blueprints.

This module is an OPTIONAL, self-contained stdlib client for the OPEA
GenAIComps microservices (https://github.com/opea-project/GenAIComps):
embeddings, LLM chat, rerank, retrieval, dataprep, guardrails, ASR and TTS.

Every call degrades gracefully: if a service is unreachable, the method returns
``None`` (or ``{}`` for health) and logs at DEBUG level.

Endpoints are resolved from opea_config (default GenAIComps ports), all
overridable via environment variables; see opea_config.MICROSERVICES.
"""

import json
import logging
import os
import urllib.error
import urllib.request

from opea_config import all_endpoints

log = logging.getLogger("opea")


def _timeout():
    try:
        return float(os.environ.get("OPEA_TIMEOUT", "10"))
    except (TypeError, ValueError):
        return 10.0


class OPEA:
    """Thin stdlib HTTP client for the OPEA GenAIComps microservices."""

    def __init__(self, endpoints=None, timeout=None):
        self.endpoints = all_endpoints()
        if endpoints:
            self.endpoints.update(endpoints)
        self.timeout = timeout if timeout is not None else _timeout()

    # -- low-level transport -------------------------------------------------

    def _request(self, name, payload=None, method="POST"):
        url = self.endpoints.get(name)
        if not url:
            return None
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype:
                    return json.loads(raw.decode("utf-8"))
                return raw
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            log.debug("OPEA %s service unreachable: %s", name, exc)
            return None

    def _post(self, name, payload):
        return self._request(name, payload=payload)

    def _get(self, name):
        return self._request(name, method="GET")

    def _reachable(self, name):
        """True if the service answers at all (even an HTTP error); False on connection failure."""
        url = self.endpoints.get(name)
        if not url:
            return False
        req = urllib.request.Request(url, method="GET")
        try:
            urllib.request.urlopen(req, timeout=self.timeout)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            return False

    # -- OPEA capabilities ---------------------------------------------------

    def embedding(self, texts):
        """Embed a list of texts. Returns list[list[float]] or None."""
        if isinstance(texts, str):
            texts = [texts]
        result = self._post("embedding", {"input": texts})
        if not result:
            return None
        data = result.get("data") or []
        return [item.get("embedding") for item in data]

    def chat(self, messages, model=None, max_tokens=None):
        """OpenAI-compatible chat completion. Returns the assistant text or None."""
        payload = {"messages": messages}
        if model:
            payload["model"] = model
        if max_tokens:
            payload["max_tokens"] = max_tokens
        result = self._post("llm", payload)
        if not result:
            return None
        try:
            return result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None

    def rerank(self, query, documents, top_n=None):
        """Rerank candidate documents for a query. Returns list of (score, text) or None."""
        payload = {"query": query, "documents": documents}
        if top_n:
            payload["top_n"] = top_n
        result = self._post("rerank", payload)
        if not result:
            return None
        out = []
        for item in result.get("documents") or result.get("results") or []:
            out.append((item.get("relevance_score") or item.get("score"),
                        item.get("text") or item.get("document")))
        return out or None

    def retrieve(self, query, k=5):
        """Retrieve relevant chunks from the OPEA retriever. Returns list[str] or None."""
        result = self._post("retriever", {"text": query, "k": k})
        if not result:
            return None
        docs = result.get("documents") or result.get("retrieved_docs") or []
        return [d.get("text") if isinstance(d, dict) else str(d) for d in docs]

    def dataprep(self, files=None, links=None):
        """Ingest documents/files into the OPEA dataprep index. Returns raw result or None."""
        payload = {}
        if files:
            payload["files"] = list(files)
        if links:
            payload["link_list"] = list(links)
        if not payload:
            return None
        return self._post("dataprep", payload)

    def asr(self, file_path):
        """Transcribe an audio file via the OPEA ASR service. Returns dict or None."""
        if not file_path or not os.path.exists(file_path):
            log.debug("OPEA ASR: no audio file provided")
            return None
        import uuid
        boundary = "----opea" + uuid.uuid4().hex
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            content = f.read()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        url = self.endpoints.get("asr")
        if not url:
            return None
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            log.debug("OPEA ASR unreachable: %s", exc)
            return None

    def tts(self, text, voice=None):
        """Synthesize speech from text via the OPEA TTS service. Returns audio bytes or None."""
        if not text:
            return None
        payload = {"input": text}
        if voice:
            payload["voice"] = voice
        return self._post("tts", payload)

    def web_search(self, query, num_results=5):
        """Search the web via the OPEA web retriever. Returns raw result or None."""
        return self._post("web_retriever", {"query": query, "num_results": num_results})

    def guardrails(self, prompt):
        """Run the OPEA guardrails service. Returns the raw result or None."""
        return self._post("guardrails", {"input": prompt})

    def health(self):
        """Best-effort reachability probe across all configured endpoints."""
        return {name: self._reachable(name) for name in self.endpoints}

    def is_available(self, name="embedding"):
        return self._reachable(name)
