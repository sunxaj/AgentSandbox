source .venv/bin/activate
BONSAI_HOST=0.0.0.0 \
PORT=8081 \
BONSAI_CTX=98304 \
BONSAI_KV4=1 \
BONSAI_MMPROJ_CPU=1 \
./scripts/start_llama_server.sh --chat-template-kwargs '{"reasoning_effort":"medium"}' --webui-mcp-proxy
