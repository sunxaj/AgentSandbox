#!/usr/bin/env python3
"""Build an Obsidian semantic index using a remote OpenAI-compatible embeddings endpoint.

The index is a local JSON artifact rather than the document Qdrant collection.
Completed embedding batches are checkpointed, and the finished file replaces
the old index atomically so a failed build leaves the previous index usable.
"""
import argparse, atexit, fcntl, hashlib, json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path

# The worker is agent-owned, but runtime_config remains the shared configuration
# module. Add the repository's MCP package explicitly for direct script runs.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MCP_DIR = PROJECT_ROOT / "mcp"
sys.path.insert(0, str(MCP_DIR))
from runtime_config import get_path, get_server_url, get_value, get_vault_folder

VAULT_DEFAULT = get_path('vault')
ENDPOINT_DEFAULT = get_server_url('embeddings_url')
CHUNK_CHARS, OVERLAP = 800, 100

def embed(endpoint, texts, timeout=180):
    """Return one vector per text, preserving input order across endpoint responses."""
    req = urllib.request.Request(endpoint, data=json.dumps({'input': texts}).encode(), headers={'Content-Type':'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: result = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Embedding API HTTP {e.code}: {e.read().decode(errors="replace")[:800]}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'Cannot reach {endpoint}: {e}')
    data = result.get('data')
    if not isinstance(data, list) or len(data) != len(texts):
        # Some compatible servers reject multi-input batches; retry singly before failing.
        if len(texts) > 1: return [embed(endpoint, [t], timeout)[0] for t in texts]
        raise RuntimeError(f'Unexpected embeddings response: {str(result)[:800]}')
    data.sort(key=lambda x: x.get('index', 0))
    vectors = [x.get('embedding') for x in data]
    if any(not isinstance(v, list) or not v for v in vectors): raise RuntimeError('Response missing embedding vector')
    return [[float(n) for n in v] for v in vectors]

def clean(text):
    """Remove formatting noise before chunking while retaining readable note text."""
    text = re.sub(r'```.*?```', ' ', text, flags=re.S)
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()

def chunks(text, size=CHUNK_CHARS, overlap=OVERLAP):
    """Pack paragraphs into bounded chunks; overlap long paragraphs at split boundaries."""
    paras = [p.strip() for p in re.split(r'\n\s*\n', clean(text)) if p.strip()]
    out, current = [], ''
    for p in paras:
        if len(p) > size:
            if current: out.append(current); current = ''
            start = 0
            while start < len(p):
                end = min(len(p), start + size); out.append(p[start:end].strip())
                if end == len(p): break
                start = max(start + 1, end - overlap)
        elif not current or len(current) + len(p) + 2 <= size:
            current = f'{current}\n\n{p}'.strip()
        else:
            out.append(current); current = p
    if current: out.append(current)
    return out

def sha(path):
    """Hash raw file bytes to distinguish content revisions reliably."""
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def acquire_lock(path):
    """Reject concurrent index builds so checkpoint and output writes cannot race."""
    lock_file = path.open('a', encoding='utf-8')
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        lock_file.close()
        raise RuntimeError('Another semantic-index rebuild is already running.') from e
    atexit.register(lock_file.close)
    return lock_file

def main():
    """Collect configured notes, resume embeddings, then atomically publish a new index."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vault', type=Path, default=VAULT_DEFAULT)
    p.add_argument('--endpoint', default=ENDPOINT_DEFAULT)
    p.add_argument('--index', type=Path, default=None)
    p.add_argument('--dirs', nargs='+', default=[get_vault_folder('knowledge').name, get_vault_folder('topics').name])
    p.add_argument('--include-inbox', action='store_true')
    p.add_argument('--batch-size', type=int, default=8)
    a = p.parse_args()
    vault = a.vault.expanduser().resolve()
    configured_index = Path(get_value('paths', 'semantic_index')).expanduser()
    index_path = (a.index or (configured_index if configured_index.is_absolute() else vault / configured_index)).resolve()
    if not vault.is_dir(): p.error(f'Vault does not exist: {vault}')
    if a.batch_size < 1: p.error('--batch-size must be >= 1')
    inbox_name = get_vault_folder('inbox').name
    dirs = list(a.dirs) + ([inbox_name] if a.include_inbox and inbox_name not in a.dirs else [])
    # Restrict indexing to configured vault folders; Inbox remains opt-in.
    files = sorted({f for d in dirs if (vault/d).is_dir() for f in (vault/d).rglob('*.md')})
    if not files: p.error('No Markdown files found; check --vault and --dirs.')
    index_path.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock(index_path.with_name('.' + index_path.name + '.lock'))
    records, texts = [], []
    for f in files:
        raw = f.read_text(encoding='utf-8', errors='replace')
        digest, rel, title = sha(f), str(f.relative_to(vault)), f.stem
        for i, chunk in enumerate(chunks(raw), 1):
            records.append({'path':rel, 'title':title, 'chunk':i, 'text':chunk, 'sha256':digest})
            texts.append(f'Note: {title}\nPath: {rel}\n\n{chunk}')
    if not records: p.error('No non-empty chunks found.')
    print(f'Found {len(files)} notes and {len(records)} chunks.')
    checkpoint_path = index_path.with_suffix(index_path.suffix + '.checkpoint.json')
    # A checkpoint is reusable only for the same ordered inputs and embedding endpoint.
    work_fingerprint = hashlib.sha256(json.dumps({
        'endpoint': a.endpoint,
        'records': [
            {'path': record['path'], 'chunk': record['chunk'],
             'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
            for record, text in zip(records, texts)
        ],
    }, sort_keys=True).encode('utf-8')).hexdigest()
    vectors = [None] * len(texts)
    if checkpoint_path.is_file():
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
            cached_vectors = checkpoint.get('vectors')
            if (checkpoint.get('format_version') == 1
                    and checkpoint.get('fingerprint') == work_fingerprint
                    and isinstance(cached_vectors, list)
                    and len(cached_vectors) == len(vectors)):
                vectors = cached_vectors
                print('Resuming embeddings from the matching checkpoint.', flush=True)
            else:
                print('Ignoring stale semantic embedding checkpoint.', flush=True)
        except (OSError, json.JSONDecodeError, AttributeError):
            print('Ignoring unreadable semantic embedding checkpoint.', flush=True)

    def save_checkpoint():
        """Atomically persist all finished vectors for this exact build fingerprint."""
        temporary = checkpoint_path.with_suffix(checkpoint_path.suffix + '.tmp')
        temporary.write_text(json.dumps({
            'format_version': 1,
            'fingerprint': work_fingerprint,
            'vectors': vectors,
        }, ensure_ascii=False), encoding='utf-8')
        temporary.replace(checkpoint_path)

    # Persist vectors after each completed API batch to make long rebuilds resumable.
    for start in range(0, len(texts), a.batch_size):
        stop = min(start + a.batch_size, len(texts))
        missing = [i for i in range(start, stop)
                   if not isinstance(vectors[i], list) or not vectors[i]]
        if missing:
            completed = embed(a.endpoint, [texts[i] for i in missing])
            if len(completed) != len(missing):
                raise RuntimeError('Embedding count did not match the requested batch')
            for i, vector in zip(missing, completed):
                vectors[i] = vector
            save_checkpoint()
        else:
            print(f'Reusing cached embeddings {stop}/{len(texts)}', flush=True)
        print(f'Embedded {stop}/{len(texts)}', flush=True)
    if any(not isinstance(v, list) or not v for v in vectors):
        raise RuntimeError('Embedding checkpoint contains missing or invalid vectors')
    dims = {len(v) for v in vectors}
    if len(vectors) != len(records) or len(dims) != 1: raise RuntimeError('Embedding count/dimensions inconsistent')
    for r, v in zip(records, vectors): r['embedding'] = v
    payload = {'format_version':1, 'created_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'vault':str(vault), 'embedding_endpoint':a.endpoint, 'embedding_dimensions':len(vectors[0]), 'document_count':len(files), 'chunk_count':len(records), 'records':records}
    # Build beside the target and replace in one filesystem operation; readers
    # therefore see either the previous complete index or this complete index.
    tmp = index_path.with_suffix(index_path.suffix+'.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8'); tmp.replace(index_path)
    try:
        checkpoint_path.unlink(missing_ok=True)
    except OSError as exc:
        print(f'WARNING: Could not remove completed checkpoint {checkpoint_path}: {exc}', file=sys.stderr)
    print(f'Index saved: {index_path} ({len(records)} chunks, {len(vectors[0])}-dimensional vectors)')

if __name__ == '__main__':
    try: main()
    except Exception as e: print(f'ERROR: {e}', file=sys.stderr); sys.exit(1)
