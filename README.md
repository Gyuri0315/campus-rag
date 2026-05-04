# campus-rag

RAG-based Q&A system for university documents

## 프로젝트 구조

- `crawler.py`: 대학 사이트의 게시글과 첨부파일을 수집합니다.
- `preprocessing.py`: 수집된 문서를 RAG에 사용할 수 있는 텍스트 형태로 전처리합니다.
- `vectorization.py`: 전처리된 텍스트를 임베딩 벡터로 변환합니다.
- `load_to_supabase.py`: 벡터화된 데이터를 Supabase PostgreSQL에 적재합니다.
- `query_supabase.py`: Supabase에 저장된 데이터를 대상으로 검색을 테스트합니다.
- `pipelining.py`: 크롤링, 전처리, 벡터화, Supabase 적재 과정을 한 번에 실행하거나 스케줄링합니다.
- `frontend/`: Next.js 기반 사용자 인터페이스입니다.
- `supabase/migrations/`: Supabase 데이터베이스 스키마 마이그레이션 SQL 파일입니다.
- `logs/`: 파이프라인 실행 로그가 저장되는 디렉터리입니다.

## 백엔드 설치

Python 가상환경을 생성한 뒤 의존성을 설치합니다.

```bash
python -m venv .venv
```

Windows PowerShell:

```bash
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS 또는 Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Supabase SQL 환경 설정

Supabase 연결을 위해 프로젝트 루트에 `.env` 파일을 생성해야 합니다.

```bash
cp .env.example .env
```

기본적으로 Supabase Pooler 연결 문자열을 사용합니다.

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

`DATABASE_URL` 대신 아래 값을 사용할 수도 있습니다.

```env
SUPABASE_DB_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

또는 PostgreSQL 접속 정보를 개별 변수로 설정할 수 있습니다.

```env
PGHOST=db.<project-ref>.supabase.co
PGPORT=5432
PGDATABASE=postgres
PGUSER=postgres
PGPASSWORD=<password>
PGSSLMODE=require
```

Supabase 데이터베이스 스키마는 `supabase/migrations/` 디렉터리의 SQL 파일로 관리합니다.

```text
supabase/migrations/001_create_rag_documents.sql
supabase/migrations/002_split_rag_documents.sql
```

Supabase Dashboard에서 SQL을 실행하는 방법은 다음과 같습니다.

1. Supabase 프로젝트 Dashboard에 접속합니다.
2. 왼쪽 메뉴에서 `SQL Editor`를 선택합니다.
3. `supabase/migrations/001_create_rag_documents.sql` 파일 내용을 붙여넣고 실행합니다.
4. 이어서 `supabase/migrations/002_split_rag_documents.sql` 파일 내용을 붙여넣고 실행합니다.

현재 적재 스크립트는 `rag_sources`, `rag_chunks` 테이블을 사용하므로 `002_split_rag_documents.sql`까지 적용되어 있어야 합니다.

## 코드 실행 방법

전체 RAG 파이프라인은 다음 순서로 실행됩니다.

1. `crawler.py`: 게시글과 첨부파일 수집
2. `preprocessing.py`: 수집 문서 전처리
3. `vectorization.py`: 텍스트 임베딩 생성
4. `load_to_supabase.py`: Supabase에 데이터 적재

각 단계를 개별로 실행할 수 있습니다.

```bash
python crawler.py
python preprocessing.py
python vectorization.py
python load_to_supabase.py
```

전체 파이프라인을 한 번만 실행하려면 다음 명령어를 사용합니다.

```bash
python pipelining.py --once
```

스케줄러를 실행하면 기본적으로 시작 즉시 한 번 실행한 뒤 매일 오전 09:00에 파이프라인을 다시 실행합니다.

```bash
python pipelining.py
```

실행 시간을 변경하려면 `--run-at` 옵션을 사용합니다.

```bash
python pipelining.py --run-at 10:30
```

Supabase에 적재된 데이터 검색을 테스트하려면 다음 명령어를 사용합니다.

```bash
python query_supabase.py "검색할 질문"
```

## 프론트엔드 실행

프론트엔드 디렉터리로 이동한 뒤 의존성을 설치하고 개발 서버를 실행합니다.

```bash
cd frontend
npm install
npm run dev
```

개발 서버 실행 후 브라우저에서 아래 주소로 접속합니다.

```text
http://localhost:3000
```
