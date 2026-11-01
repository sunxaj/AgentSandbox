exec ../llama/build/bin/llama-server \
  -hf ggml-org/embeddinggemma-300M-GGUF:Q8_0 \
  --embeddings \
  --host 0.0.0.0 \
  --port 8082
