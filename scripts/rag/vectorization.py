"""preprocessed JSON을 RAG 검색용 벡터 파일로 변환하는 스크립트.

동작 요약:
1. /files/ce/preprocessed 아래의 전처리 JSON 파일을 모두 찾는다.
2. 각 JSON의 chunks 배열에서 검색 단위 텍스트와 출처 메타데이터를 꺼낸다.
3. 기본값으로 추가 패키지 없이 동작하는 해시 기반 임베딩을 생성한다.
   sentence-transformers가 설치되어 있으면 옵션으로 의미 기반 임베딩도 사용할 수 있다.
4. 파일별 벡터 결과는 /files/ce/vectorized에 같은 폴더 구조로 저장한다.
5. 전체 청크를 한 번에 불러오기 쉬운 /files/ce/vectorized/index.jsonl과
   실행 요약인 /files/ce/vectorized/manifest.json을 함께 만든다.

기본 실행:
    python scripts/rag/vectorization.py

실행 전 파일 확인:
    python scripts/rag/vectorization.py --dry-run

sentence-transformers 사용:
    python scripts/rag/vectorization.py --backend sentence-transformers
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "vectorization.log"


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )

# CLI 기본값: 입력/출력 경로와 임베딩 방식 설정.
DATASET_PATHS = {
    "ce": {
        "input_root": PROJECT_ROOT / "files" / "ce" / "preprocessed",
        "output_root": PROJECT_ROOT / "files" / "ce" / "vectorized",
    },
    "pknu_notice": {
        "input_root": PROJECT_ROOT / "files" / "pknu_notice" / "preprocessed",
        "output_root": PROJECT_ROOT / "files" / "pknu_notice" / "vectorized",
    },
    "pknu_student_life": {
        "input_root": PROJECT_ROOT / "files" / "pknu_student_life" / "preprocessed",
        "output_root": PROJECT_ROOT / "files" / "pknu_student_life" / "vectorized",
    },
    "rule": {
        "input_root": PROJECT_ROOT / "files" / "rule" / "preprocessed",
        "output_root": PROJECT_ROOT / "files" / "rule" / "vectorized",
    },
}
DEFAULT_DATASET = "ce"
DEFAULT_BACKEND = "sentence-transformers"
DEFAULT_DIMENSIONS = 768
DEFAULT_BATCH_SIZE = 32
DEFAULT_SENTENCE_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MIN_CHUNK_CHARS = 40
MIN_UNIQUE_TOKENS = 4
MIN_META_STUB_CHARS = 100
MAX_META_STUB_UNIQUE_TOKENS = 6

ACADEMIC_KEYWORDS = (
    "학사",
    "수강",
    "졸업",
    "전공",
    "복수전공",
    "부전공",
    "전과",
    "성적",
    "학점",
    "장학",
    "휴학",
    "복학",
    "등록",
    "신청",
    "기간",
    "자격",
    "조건",
    "이수",
    "교직",
    "현장실습",
)

NOISE_PHRASES = (
    "개인정보 수집",
    "저작물 활용 동의서",
    "접수번호는 공란",
    "글자크기",
    "연구보고",
    "contents",
    "목 차",
)

GENERIC_STUB_TITLES = (
    "학부 소개",
    "학부소개",
    "졸업 후 진로",
    "졸업후진로",
    "교육과정",
    "국립 부경대학교 대학생활 E-하나로",
    "대학생활 E-하나로",
)


class NoChunksError(ValueError):
    """전처리 JSON에 사용할 수 있는 청크가 없을 때 발생하는 예외."""

    pass


class Embedder(Protocol):
    """해시 임베딩과 모델 임베딩이 공통으로 따라야 하는 인터페이스."""

    name: str
    dimensions: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


def normalize_text(text: str) -> str:
    """임베딩 또는 저장 전에 텍스트의 공백을 일정하게 정리한다."""

    if not text:
        return ""
    return " ".join(text.replace("\u00a0", " ").split()).strip()


def _tokenize_for_quality(text: str) -> list[str]:
    return re.findall(r"[0-9A-Za-z\uac00-\ud7a3]+", normalize_text(text).lower())


def assess_chunk_quality(text: str) -> tuple[bool, list[str]]:
    """검색 품질을 떨어뜨리는 빈약한 청크를 걸러낸다."""

    normalized = normalize_text(text)
    flags: list[str] = []
    if not normalized:
        return False, ["empty"]

    tokens = _tokenize_for_quality(normalized)
    unique_tokens = set(tokens)
    hangul_count = len(re.findall(r"[\uac00-\ud7a3]", normalized))
    digit_count = len(re.findall(r"\d", normalized))
    dot_count = normalized.count(".") + normalized.count("·") + normalized.count("_")
    compact_len = max(1, len(re.sub(r"\s+", "", normalized)))
    has_academic_keyword = any(keyword in normalized for keyword in ACADEMIC_KEYWORDS)
    academic_keyword_hits = sum(
        1 for keyword in ACADEMIC_KEYWORDS if keyword in normalized
    )
    lower_text = normalized.lower()
    has_noise_phrase = any(phrase.lower() in lower_text for phrase in NOISE_PHRASES)

    if len(normalized) < MIN_CHUNK_CHARS and not has_academic_keyword:
        flags.append("too_short")
    if len(unique_tokens) < MIN_UNIQUE_TOKENS and not has_academic_keyword:
        flags.append("low_unique_tokens")
    if (
        len(normalized) < MIN_META_STUB_CHARS
        and len(unique_tokens) <= MAX_META_STUB_UNIQUE_TOKENS
        and academic_keyword_hits < 3
    ):
        flags.append("metadata_stub")
    if dot_count / compact_len >= 0.20:
        flags.append("punctuation_heavy")
    if digit_count / compact_len >= 0.60 and hangul_count < 20:
        flags.append("numeric_heavy")
    if re.fullmatch(r"[\d\s.,:;~\-–—·ㆍ/()]+", normalized):
        flags.append("no_words")
    if has_noise_phrase and len(normalized) < 300:
        flags.append("template_or_form_noise")

    return not flags, flags


def assess_record_quality(text: str, metadata: dict) -> tuple[bool, list[str]]:
    is_useful, flags = assess_chunk_quality(text)
    title = normalize_text(
        str(metadata.get("doc_title") or metadata.get("source_file") or "")
    )
    normalized = normalize_text(text)
    title_compact = re.sub(r"\s+", "", title)
    text_compact = re.sub(r"\s+", "", normalized)

    if title_compact in {
        re.sub(r"\s+", "", item) for item in GENERIC_STUB_TITLES
    } and len(normalized) < 160:
        flags.append("generic_title_stub")

    if title_compact and text_compact and title_compact in text_compact:
        remainder = text_compact.replace(title_compact, "")
        if len(normalized) < 180 and len(remainder) <= 20:
            flags.append("title_only_stub")

    return not flags, flags


def build_context_prefix(metadata: dict, source_file: str) -> str:
    """문서 제목/분류를 본문 앞에 붙여 짧은 청크의 검색 문맥을 보강한다."""

    fields = [
        ("문서제목", metadata.get("doc_title")),
        ("분류", metadata.get("category")),
        ("세부분류", metadata.get("subcategory")),
        ("공지주제", metadata.get("notice_topic")),
        ("첨부명", metadata.get("attachment_name")),
        ("파일명", source_file),
    ]
    lines: list[str] = []
    seen: set[str] = set()
    for label, value in fields:
        text = normalize_text(str(value or ""))
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def build_embedding_text(text: str, metadata: dict, source_file: str) -> str:
    prefix = build_context_prefix(metadata, source_file)
    body = normalize_text(text)
    if not prefix:
        return body
    return f"{prefix}\n본문: {body}"


def rel_project_path(path: Path, root: Path) -> str:
    """가능하면 프로젝트 기준 상대 경로를 반환하고, 불가능하면 절대 경로를 반환한다."""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def stable_id(*parts: object, length: int = 16) -> str:
    """출처 정보와 청크 정보를 바탕으로 항상 같은 짧은 ID를 만든다."""

    raw = "::".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def iter_json_files(input_root: Path) -> list[Path]:
    """입력 폴더 아래의 모든 전처리 JSON 파일을 찾는다."""

    if not input_root.exists():
        return []
    return sorted(p for p in input_root.rglob("*.json") if p.is_file())


def output_path_for(input_file: Path, input_root: Path, output_root: Path) -> Path:
    """전처리 파일의 하위 경로 구조를 vectorized 폴더 아래에 그대로 대응시킨다."""

    rel = input_file.resolve().relative_to(input_root.resolve())
    return output_root / rel


def is_vectorized_current(input_file: Path, input_root: Path, output_root: Path) -> bool:
    out_path = output_path_for(input_file, input_root, output_root)
    return out_path.exists() and out_path.stat().st_mtime >= input_file.stat().st_mtime


def batched(items: list[str], batch_size: int) -> Iterable[list[str]]:
    """임베딩 호출을 효율적으로 하기 위해 항목을 고정 크기 묶음으로 나눈다."""

    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def token_features(text: str) -> Counter[str]:
    """HashEmbedder가 사용할 토큰과 문자 n-gram 특징을 추출한다."""

    text = normalize_text(text).lower()
    features: Counter[str] = Counter()

    # 숫자, 영문 단어, 한글 음절을 토큰으로 인식한다.
    for token in re.findall(r"[0-9a-zA-Z\uac00-\ud7a3]+", text):
        features[f"tok:{token}"] += 1
        if len(token) >= 4:
            # 긴 토큰은 3글자 조각도 추가해 부분 일치 성능을 보완한다.
            for i in range(len(token) - 2):
                features[f"tri:{token[i:i + 3]}"] += 1

    compact = re.sub(r"\s+", " ", text)
    for i in range(max(0, len(compact) - 2)):
        gram = compact[i : i + 3]
        if gram.strip():
            features[f"chr:{gram}"] += 1

    return features


class HashEmbedder:
    """추가 패키지 없이 동작하는 결정적 해시 기반 임베딩.

    의미를 학습한 모델은 아니며, 텍스트 특징을 고정 차원 벡터에 해시해서 넣는 방식이다.
    sentence-transformers 같은 모델을 설치하기 전에도 전체 파이프라인을 실행할 수 있도록
    기본 검색용 벡터를 제공한다.
    """

    def __init__(self, dimensions: int) -> None:
        """고정 벡터 차원 수를 설정한다."""

        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.name = "hash"
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """여러 텍스트를 해시 기반 방식으로 벡터화한다."""

        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        """하나의 텍스트 청크를 L2 정규화된 벡터로 변환한다."""

        vector = [0.0] * self.dimensions
        features = token_features(text)
        if not features:
            return vector

        for feature, count in features.items():
            # feature hashing으로 임의의 텍스트 특징을 고정 길이 벡터 인덱스에 배치한다.
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            hashed = int.from_bytes(digest, byteorder="big", signed=False)
            index = hashed % self.dimensions
            sign = -1.0 if ((hashed >> 8) & 1) else 1.0
            vector[index] += sign * (1.0 + math.log(count))

        # 정규화하면 코사인 유사도를 단순 내적으로 계산할 수 있다.
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [round(value / norm, 8) for value in vector]
        return vector


class SentenceTransformerEmbedder:
    """sentence-transformers를 사용하는 선택형 의미 기반 임베딩 백엔드."""

    def __init__(self, model_name: str) -> None:
        """지정한 SentenceTransformer 모델을 로드한다."""

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Install it or run with --backend hash."
            ) from exc

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers:{model_name}"
        dim = self.model.get_sentence_embedding_dimension()
        self.dimensions = int(dim) if dim else 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """텍스트 청크 묶음을 모델 기반 정규화 벡터로 변환한다."""

        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[round(float(value), 8) for value in row] for row in vectors]


def make_embedder(args: argparse.Namespace) -> Embedder:
    """CLI 옵션에 따라 사용할 임베딩 백엔드를 생성한다."""

    if args.backend == "hash":
        return HashEmbedder(args.dimensions)
    if args.backend == "sentence-transformers":
        return SentenceTransformerEmbedder(args.model_name)
    raise ValueError(f"unsupported backend: {args.backend}")


def resolve_vector_roots(args: argparse.Namespace) -> tuple[Path, Path]:
    """데이터셋 기본 경로를 사용하되, CLI에서 직접 지정한 경로를 우선한다."""

    dataset_paths = DATASET_PATHS[args.dataset]
    input_root = args.input_root or dataset_paths["input_root"]
    output_root = args.output_root or dataset_paths["output_root"]
    return input_root, output_root


def extract_chunk_records(doc: dict, input_file: Path, project_root: Path) -> list[dict]:
    """전처리 청크를 아직 embedding이 없는 검색 record로 변환한다."""

    chunks = doc.get("chunks") or []
    if not isinstance(chunks, list):
        return []

    source_slug = doc.get("slug") or stable_id(rel_project_path(input_file, project_root))
    provenance = doc.get("provenance") or {}
    # RAG 답변에서 출처를 표시할 수 있도록 모든 청크에 provenance 메타데이터를 보존한다.
    base_metadata = {
        "source_slug": source_slug,
        "source_file": doc.get("source_file", ""),
        "source_path": doc.get("source_path", ""),
        "source_relative_to_input": doc.get("source_relative_to_input", ""),
        "source_ext": doc.get("source_ext", ""),
        "preprocessed_path": rel_project_path(input_file, project_root),
        "doc_title": provenance.get("doc_title", ""),
        "doc_url": provenance.get("doc_url", ""),
        "category": provenance.get("category", ""),
        "subcategory": provenance.get("subcategory", ""),
        "doc_type": provenance.get("doc_type", ""),
        "source_kind": doc.get("source_kind", provenance.get("source_kind", "")),
        "date": provenance.get("date", ""),
        "is_notice": provenance.get("is_notice", False),
        "crawled_at": provenance.get("crawled_at", ""),
        "attachment_name": provenance.get("attachment_name", ""),
        "attachment_url": provenance.get("attachment_url", ""),
        "attachment_kind": provenance.get("attachment_kind", ""),
        "attachments": provenance.get("attachments", []),
        "document_kind": provenance.get("document_kind", ""),
        "is_form": provenance.get("is_form", False),
        "is_appendix_table": provenance.get("is_appendix_table", False),
        "form_number": provenance.get("form_number", ""),
        "appendix_number": provenance.get("appendix_number", ""),
        "notice_topic": provenance.get("notice_topic", ""),
        "source_page_url": provenance.get("source_page_url", ""),
        "source_site": provenance.get("source_site", ""),
        "source_json_path": provenance.get("source_json_path", ""),
        "source_html_path": provenance.get("source_html_path", ""),
        "source_attachment_path": provenance.get("source_attachment_path", ""),
    }

    records: list[dict] = []
    skipped_quality = 0
    for position, chunk in enumerate(chunks, start=1):
        # 잘못된 형식이거나 비어 있는 청크는 전체 파일 실패로 처리하지 않고 건너뛴다.
        if not isinstance(chunk, dict):
            continue
        raw_text = normalize_text(chunk.get("text", ""))
        if not raw_text:
            continue
        is_useful, quality_flags = assess_record_quality(raw_text, base_metadata)
        if not is_useful:
            skipped_quality += 1
            continue

        text = build_embedding_text(raw_text, base_metadata, doc.get("source_file", ""))

        chunk_id = chunk.get("chunk_id", position)
        chunk_index = position - 1
        record_id = stable_id(source_slug, chunk_index)
        # 이 record 하나가 벡터 인덱스에서 검색되는 최소 단위가 된다.
        records.append(
            {
                "id": record_id,
                "source_slug": source_slug,
                "chunk_id": chunk_id,
                "chunk_index": chunk_index,
                "text": text,
                "metadata": {
                    **base_metadata,
                    "num_chars": chunk.get("num_chars", len(raw_text)),
                    "indexed_num_chars": len(text),
                    "num_lines": chunk.get("num_lines"),
                    "quality_flags": quality_flags,
                    "quality_context_prefix": bool(
                        build_context_prefix(base_metadata, doc.get("source_file", ""))
                    ),
                },
            }
        )
    if skipped_quality:
        log.debug(
            "Skipped %d low-quality chunks from %s",
            skipped_quality,
            rel_project_path(input_file, project_root),
        )
    return records


def vectorize_file(
    input_file: Path,
    input_root: Path,
    output_root: Path,
    project_root: Path,
    embedder: Embedder,
    batch_size: int,
    index_handle,
) -> tuple[int, Path]:
    """전처리 JSON 파일 하나를 벡터화하고 파일별 결과와 JSONL 인덱스를 저장한다."""

    doc = json.loads(input_file.read_text(encoding="utf-8-sig"))
    records = extract_chunk_records(doc, input_file, project_root)
    if not records:
        raise NoChunksError("no chunks found")

    texts = [record["text"] for record in records]
    vectors: list[list[float]] = []
    for batch in batched(texts, batch_size):
        # 모델 기반 임베딩에서는 배치 처리가 성능에 중요하다.
        vectors.extend(embedder.embed_texts(batch))

    for record, vector in zip(records, vectors):
        record["embedding"] = vector

    # vectorized 폴더 아래에도 preprocessed와 같은 하위 경로 구조를 유지한다.
    out_path = output_path_for(input_file, input_root, output_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "source_preprocessed_path": rel_project_path(input_file, project_root),
        "vectorized_at": datetime.now().isoformat(),
        "embedding_backend": embedder.name,
        "embedding_dimensions": embedder.dimensions,
        "num_chunks": len(records),
        "records": records,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 평평한 JSONL 인덱스는 DB 삽입이나 벡터 스토어 적재에 편하다.
    for record in records:
        index_handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return len(records), out_path


def run_batch(
    input_root: Path,
    output_root: Path,
    project_root: Path,
    embedder: Embedder,
    batch_size: int,
    dry_run: bool,
    dataset: str,
    changed_only: bool = False,
    target_files: list[Path] | None = None,
) -> None:
    """입력 폴더 아래의 모든 전처리 JSON 파일을 벡터화한다."""

    if target_files is None:
        files = iter_json_files(input_root)
    else:
        files = []
        for path in target_files:
            resolved = path.resolve()
            if not resolved.exists() or resolved.suffix.lower() != ".json":
                continue
            try:
                resolved.relative_to(input_root)
            except ValueError:
                continue
            files.append(resolved)
        files = sorted(dict.fromkeys(files))
    if not files:
        log.info("No preprocessed JSON files found under %s", input_root)
        return

    total_files = len(files)
    if changed_only:
        unchanged_files = [
            path
            for path in files
            if is_vectorized_current(path, input_root, output_root)
        ]
        files = [path for path in files if path not in set(unchanged_files)]
        log.info(
            "Found %d preprocessed files (%d changed, %d unchanged)",
            total_files,
            len(files),
            len(unchanged_files),
        )
    else:
        log.info("Found %d preprocessed files", len(files))
    if dry_run:
        for path in files:
            log.info("[DRY-RUN] %s", rel_project_path(path, project_root))
        return

    output_root.mkdir(parents=True, exist_ok=True)
    index_path = output_root / "index.jsonl"
    manifest_path = output_root / "manifest.json"

    ok = 0
    skipped = 0
    failed = 0
    total_chunks = 0
    skipped_files: list[dict] = []
    failures: list[dict] = []

    with index_path.open("w", encoding="utf-8") as index_handle:
        for file_path in files:
            rel = rel_project_path(file_path, project_root)
            try:
                chunk_count, out_path = vectorize_file(
                    input_file=file_path,
                    input_root=input_root,
                    output_root=output_root,
                    project_root=project_root,
                    embedder=embedder,
                    batch_size=batch_size,
                    index_handle=index_handle,
                )
                ok += 1
                total_chunks += chunk_count
                log.info("[OK] %s -> %s (%d chunks)", rel, out_path.as_posix(), chunk_count)
            except NoChunksError as exc:
                # 이미지 기반 PDF처럼 텍스트 추출 결과가 비어 있는 파일은 skip으로 기록한다.
                skipped += 1
                skipped_files.append({"path": rel, "reason": str(exc)})
                log.info("[SKIP] %s (%s)", rel, exc)
            except Exception as exc:
                # 예상하지 못한 오류는 skip과 구분해 failed로 기록한다.
                failed += 1
                failures.append({"path": rel, "error": str(exc)})
                log.warning("[FAIL] %s (%s)", rel, exc)

    # manifest는 이번 벡터화 실행 결과를 추적하기 위한 요약 파일이다.
    manifest = {
        "vectorized_at": datetime.now().isoformat(),
        "dataset": dataset,
        "input_root": rel_project_path(input_root, project_root),
        "output_root": rel_project_path(output_root, project_root),
        "embedding_backend": embedder.name,
        "embedding_dimensions": embedder.dimensions,
        "num_files": len(files),
        "num_files_seen": total_files,
        "num_files_vectorized": ok,
        "num_files_skipped": skipped,
        "num_files_failed": failed,
        "num_chunks": total_chunks,
        "index_path": rel_project_path(index_path, project_root),
        "skipped_files": skipped_files,
        "failures": failures,
        "changed_only": changed_only,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Done: files=%d, chunks=%d, skipped=%d, failed=%d", ok, total_chunks, skipped, failed)


def main() -> None:
    """CLI 인자를 파싱하고 전체 벡터화 작업을 시작한다."""

    parser = argparse.ArgumentParser(
        description="Vectorize preprocessed chunk JSON files for RAG retrieval."
    )
    parser.add_argument(
        "--dataset",
        choices=sorted(DATASET_PATHS),
        default=DEFAULT_DATASET,
        help=(
            "Dataset path preset to vectorize. "
            "Use ce for files/ce/preprocessed -> files/ce/vectorized, "
            "pknu_notice for files/pknu_notice/preprocessed -> files/pknu_notice/vectorized, "
            "or rule for files/rule/preprocessed -> files/rule/vectorized."
        ),
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=None,
        help="Preprocessed JSON root directory. Overrides --dataset input path.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Vectorized output root directory. Overrides --dataset output path.",
    )
    parser.add_argument(
        "--backend",
        choices=["hash", "sentence-transformers"],
        default=DEFAULT_BACKEND,
        help="Embedding backend. Use hash for a dependency-free baseline.",
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_SENTENCE_MODEL,
        help="SentenceTransformer model name when --backend sentence-transformers is used.",
    )
    parser.add_argument(
        "--dimensions",
        type=int,
        default=DEFAULT_DIMENSIONS,
        help="Hash embedding dimensions when --backend hash is used.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Embedding batch size.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List target files without writing vectorized outputs.",
    )
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="Vectorize only files whose vectorized JSON is missing or older than the preprocessed input.",
    )
    args = parser.parse_args()

    configure_logging()

    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")

    # 현재 작업 디렉터리를 프로젝트 루트로 사용한다.
    project_root = PROJECT_ROOT
    input_root, output_root = resolve_vector_roots(args)
    embedder = HashEmbedder(1) if args.dry_run else make_embedder(args)
    run_batch(
        input_root=input_root,
        output_root=output_root,
        project_root=project_root,
        embedder=embedder,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        dataset=args.dataset,
        changed_only=args.changed_only,
    )


if __name__ == "__main__":
    main()
