# Code Examples — `DataReply/makeathon`

> Copy-paste-ready index of the official starter repo so agents don't need to browse GitHub. Full repo: [DataReply/makeathon](https://github.com/DataReply/makeathon).

## Python

### Dependencies ([`requirements.txt`](https://github.com/DataReply/makeathon/blob/main/requirements.txt))

```text
boto3
python-dotenv
pandas
langchain-aws
langchain-text-splitters
lxml
beautifulsoup4
langchain
langgraph
```

Setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### [`python/py/bedrock_local.py`](https://github.com/DataReply/makeathon/blob/main/python/py/bedrock_local.py) — minimal Bedrock call with `boto3`

```python
import os

import boto3
from dotenv import load_dotenv

load_dotenv()

brt = boto3.client(
    service_name="bedrock-runtime",
    region_name="eu-central-1",
)

model_id = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

user_message = "Describe the purpose of a 'hello world' program in one line."
conversation = [
    {
        "role": "user",
        "content": [{"text": user_message}],
    }
]

response = brt.converse(
    modelId=model_id,
    messages=conversation,
    inferenceConfig={"maxTokens": 512, "temperature": 0.5},
)

response_text = response["output"]["message"]["content"][0]["text"]
print("Model Response:", response_text)
```

Uses the Bedrock **Converse API** (`brt.converse(...)`) — the recommended cross-model API. Always use an `eu.` inference-profile ID.

### [`python/py/s3_local.py`](https://github.com/DataReply/makeathon/blob/main/python/py/s3_local.py) — create bucket, upload + download

```python
import boto3
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

bucket_name = "CHANGE_TO_UNIQUE_NAME"   # must be globally unique
prefix = "data/"

s3_resource = boto3.resource('s3')
existing_buckets = [bucket.name for bucket in s3_resource.buckets.all()]
s3_client = boto3.client('s3', region_name="eu-central-1")
if bucket_name not in existing_buckets:
    s3_client.create_bucket(
        Bucket=bucket_name,
        CreateBucketConfiguration={'LocationConstraint': 'eu-central-1'},
    )
    print("Created new bucket")
else:
    print("Bucket already exists")

data = {
    'name': ['Alice', 'Bob', 'Charlie'],
    'age': [25, 30, 35],
    'email': ['alice@example.com', 'bob@example.com', 'charlie@example.com'],
}
df = pd.DataFrame(data)

local_file_path = "dummy_data.csv"
df.to_csv(local_file_path, index=False)

s3_key = prefix + "dummy_data.csv"
s3_client.upload_file(Filename=local_file_path, Bucket=bucket_name, Key=s3_key)

s3_client.download_file(Filename="dummy_data.csv", Bucket=bucket_name, Key=s3_key)
```

### Notebooks (linked, not inlined)

- [`python/notebooks/Bedrock_Example.ipynb`](https://github.com/DataReply/makeathon/blob/main/python/notebooks/Bedrock_Example.ipynb) — invoke Bedrock from a SageMaker notebook.
- [`python/notebooks/S3_Example.ipynb`](https://github.com/DataReply/makeathon/blob/main/python/notebooks/S3_Example.ipynb) — read/write S3 from a notebook.
- [`python/notebooks/RAG_agent_example.ipynb`](https://github.com/DataReply/makeathon/blob/main/python/notebooks/RAG_agent_example.ipynb) — **most useful for this challenge**. A LangGraph agent that uses S3 Vectors as a similarity-search tool over an HTML input. Good scaffolding for the Campus Co-Pilot.

## TypeScript

All scripts live in the [`typescript/`](https://github.com/DataReply/makeathon/tree/main/typescript) folder. Run `npm install` first.

| Command | File | What it does |
|---|---|---|
| `npm run verify` | [`src/verify.ts`](https://github.com/DataReply/makeathon/blob/main/typescript/src/verify.ts) | Load `.env`, call `sts:GetCallerIdentity`. Sanity-check credentials. |
| `npm run bedrock` | [`src/bedrock.ts`](https://github.com/DataReply/makeathon/blob/main/typescript/src/bedrock.ts) | Invoke any Bedrock model — simple + streaming (`InvokeModelCommand`, `InvokeModelWithResponseStreamCommand`). |
| `npm run s3` | [`src/s3.ts`](https://github.com/DataReply/makeathon/blob/main/typescript/src/s3.ts) | Upload / download / list S3 objects (`PutObjectCommand`, `GetObjectCommand`). |
| `npm run rag` | [`src/rag.ts`](https://github.com/DataReply/makeathon/blob/main/typescript/src/rag.ts) | Full RAG pipeline on S3 Vectors — create bucket + index, embed with Titan v2, `PutVectorsCommand`, `QueryVectorsCommand`, answer with Claude. |
| `npm run langchain` | [`src/langchain-rag.ts`](https://github.com/DataReply/makeathon/blob/main/typescript/src/langchain-rag.ts) | Same RAG pipeline via `@langchain/aws` (`ChatBedrockConverse` + `BedrockEmbeddings` + `MemoryVectorStore`). |

All scripts read config from `src/config.ts`, which loads `.env` via `dotenv` and exposes a typed config object.

## Reference backend pattern — Express + Bedrock + S3 Vectors

Minimal server from [`typescript/README.md`](https://github.com/DataReply/makeathon/blob/main/typescript/README.md). Use as a starting point for a JS/TS backend. (This repo's backend is Python/FastAPI — see the FastAPI port below.)

```typescript
// server.ts
import express from "express";
import {
  BedrockRuntimeClient,
  InvokeModelCommand,
} from "@aws-sdk/client-bedrock-runtime";
import {
  S3VectorsClient,
  PutVectorsCommand,
  QueryVectorsCommand,
} from "@aws-sdk/client-s3vectors";
import "dotenv/config";

const app = express();
app.use(express.json());

const bedrock = new BedrockRuntimeClient({ region: "eu-central-1" });
const s3v = new S3VectorsClient({ region: "eu-central-1" });

const CHAT_MODEL = process.env.BEDROCK_CHAT_MODEL ?? "eu.anthropic.claude-sonnet-4-6";
const EMBED_MODEL = process.env.BEDROCK_EMBEDDING_MODEL ?? "amazon.titan-embed-text-v2:0";
const VECTOR_BUCKET = process.env.S3_VECTOR_BUCKET ?? "hackathon-team-XX-vectors";
const VECTOR_INDEX = process.env.S3_VECTOR_INDEX ?? "knowledge-base";

async function embed(text: string): Promise<number[]> {
  const res = await bedrock.send(new InvokeModelCommand({
    modelId: EMBED_MODEL,
    contentType: "application/json",
    accept: "application/json",
    body: JSON.stringify({ inputText: text }),
  }));
  const body = JSON.parse(new TextDecoder().decode(res.body));
  return body.embedding;
}

app.post("/ask", async (req, res) => {
  const { question } = req.body;
  const queryVector = await embed(question);

  const searchResult = await s3v.send(new QueryVectorsCommand({
    vectorBucketName: VECTOR_BUCKET,
    indexName: VECTOR_INDEX,
    queryVector: { float32: queryVector },
    topK: 3,
    returnMetadata: true,
  }));

  const context = (searchResult.vectors ?? [])
    .map((v: any, i: number) => `[${i + 1}] ${v.metadata?.source_text ?? ""}`)
    .join("\n\n");

  const payload = {
    anthropic_version: "bedrock-2023-05-31",
    max_tokens: 1024,
    system: `Answer based ONLY on this context:\n${context}`,
    messages: [{ role: "user", content: question }],
  };

  const llmResult = await bedrock.send(new InvokeModelCommand({
    modelId: CHAT_MODEL,
    contentType: "application/json",
    accept: "application/json",
    body: JSON.stringify(payload),
  }));

  const answer = JSON.parse(new TextDecoder().decode(llmResult.body));
  res.json({ answer: answer.content[0].text, sources: searchResult.vectors });
});

app.post("/ingest", async (req, res) => {
  const { documents } = req.body;
  const vectors = [];
  for (const doc of documents) {
    const embedding = await embed(doc.text);
    vectors.push({
      key: doc.key,
      data: { float32: embedding },
      metadata: { source_text: doc.text, ...doc.metadata },
    });
  }
  await s3v.send(new PutVectorsCommand({
    vectorBucketName: VECTOR_BUCKET,
    indexName: VECTOR_INDEX,
    vectors,
  }));
  res.json({ ingested: vectors.length });
});

app.listen(3000);
```

### FastAPI port hint (this repo's backend is FastAPI)

Translate the same flow into the existing [`backend/app/main.py`](../backend/app/main.py):

```python
import os
import boto3
from fastapi import FastAPI
from pydantic import BaseModel

REGION = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
CHAT_MODEL = os.environ["BEDROCK_CHAT_MODEL"]          # e.g. eu.anthropic.claude-sonnet-4-6
EMBED_MODEL = os.environ["BEDROCK_EMBEDDING_MODEL"]    # amazon.titan-embed-text-v2:0
VECTOR_BUCKET = os.environ["S3_VECTOR_BUCKET"]
VECTOR_INDEX = os.environ["S3_VECTOR_INDEX"]

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
s3v = boto3.client("s3vectors", region_name=REGION)

app = FastAPI()


class AskRequest(BaseModel):
    question: str


def embed(text: str) -> list[float]:
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        contentType="application/json",
        accept="application/json",
        body=b'{"inputText": %s}' % text.encode(),
    )
    import json
    return json.loads(resp["body"].read())["embedding"]


@app.post("/ask")
def ask(req: AskRequest):
    vec = embed(req.question)
    search = s3v.query_vectors(
        vectorBucketName=VECTOR_BUCKET,
        indexName=VECTOR_INDEX,
        queryVector={"float32": vec},
        topK=3,
        returnMetadata=True,
    )
    context = "\n\n".join(
        f"[{i+1}] {v['metadata'].get('source_text', '')}"
        for i, v in enumerate(search.get("vectors", []))
    )
    answer = bedrock.converse(
        modelId=CHAT_MODEL,
        system=[{"text": f"Answer ONLY from this context:\n{context}"}],
        messages=[{"role": "user", "content": [{"text": req.question}]}],
        inferenceConfig={"maxTokens": 1024},
    )
    return {
        "answer": answer["output"]["message"]["content"][0]["text"],
        "sources": search.get("vectors", []),
    }
```

Add `boto3` and `python-dotenv` to [`backend/requirements.txt`](../backend/requirements.txt) as needed.

### Key takeaways (for any stack)

1. The AWS SDK is just a library — import, create a client, `await client.send(...)` (JS) or `client.converse(...)` (Python).
2. Create clients **once** at module scope. The SDK pools connections internally.
3. Credentials are read from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` automatically — don't pass them to the constructor.
4. Use **inference-profile IDs** (`eu.`-prefixed), not raw model IDs.
5. **Embeddings + S3 Vectors replace your typical SQL `SELECT`** when doing semantic search. Embed the query, `QueryVectorsCommand`, stuff top-K into a prompt.

## Tips

- **LangChain docs MCP server:** give your coding assistant live LangChain/LangGraph/LangSmith docs at `https://docs.langchain.com/mcp`.
  ```json
  {
    "mcpServers": {
      "langchain-docs": {
        "type": "http",
        "url": "https://docs.langchain.com/mcp"
      }
    }
  }
  ```
- **AWS Quickstart slides:** [`Makeathon AWS Quickstart Guide.pdf`](https://github.com/DataReply/makeathon/blob/main/Makeathon%20AWS%20Quickstart%20Guide.pdf) covers login, roles/permissions, access keys, SageMaker, and S3 step-by-step.
- Never commit `.env`; it's already ignored in the example repo.
