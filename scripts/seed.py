"""Load sample data into a local Weaviate for developing and testing weavtui.

Usage:
    uv run python scripts/seed.py [--host localhost] [--port 8080] [--grpc-port 50051] [--reset]

Creates three collections:
    WeavtuiArticle  - 120 objects covering most property types, with random 8-dim vectors
    WeavtuiAuthor   - 15 objects, no vectors
    WeavtuiJournal  - 6 objects, no vectors
aliases Articles, Papers -> WeavtuiArticle and People -> WeavtuiAuthor, and cross-references
WeavtuiArticle.writtenBy -> WeavtuiAuthor, WeavtuiArticle.cites -> WeavtuiArticle,
WeavtuiArticle.publishedIn -> WeavtuiJournal, WeavtuiAuthor.favourite -> WeavtuiArticle and
WeavtuiJournal.editor -> WeavtuiAuthor.
Existing collections with these names are left untouched unless --reset is given.
"""

import argparse
import random
import warnings
from datetime import UTC, datetime, timedelta

with warnings.catch_warnings(record=True):  # authlib deprecation warning on import
    import weaviate
    from weaviate.classes.config import Configure, DataType, Property, ReferenceProperty

ARTICLE = "WeavtuiArticle"
AUTHOR = "WeavtuiAuthor"
JOURNAL = "WeavtuiJournal"

WORDS = [
    "vector",
    "database",
    "search",
    "semantic",
    "index",
    "graph",
    "neural",
    "model",
    "query",
    "embedding",
    "cluster",
    "shard",
    "tenant",
    "replica",
    "schema",
    "property",
    "filter",
    "ranking",
    "keyword",
    "hybrid",
]
COUNTRIES = ["Czechia", "Slovakia", "Germany", "Austria", "Poland", "France"]
TAGS = ["nlp", "ir", "ml", "db", "systems", "theory"]


def sentence(rng: random.Random, n: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n)).capitalize() + "."


def seed(client: weaviate.WeaviateClient, reset: bool) -> None:
    rng = random.Random(42)
    for name in (ARTICLE, AUTHOR, JOURNAL):
        if client.collections.exists(name) and not reset:
            print(f"{name} already exists, skipping (use --reset to recreate)")
            return
    for name in (ARTICLE, AUTHOR, JOURNAL):
        client.collections.delete(name)

    authors = client.collections.create(
        AUTHOR,
        description="Sample authors",
        vector_config=Configure.Vectors.self_provided(),
        properties=[
            Property(name="name", data_type=DataType.TEXT),
            Property(name="country", data_type=DataType.TEXT),
            Property(name="h_index", data_type=DataType.INT),
        ],
    )
    authors.data.insert_many(
        [
            {
                "name": f"Author {i:02d}",
                "country": rng.choice(COUNTRIES),
                "h_index": rng.randint(1, 60),
            }
            for i in range(15)
        ]
    )

    journals = client.collections.create(
        JOURNAL,
        description="Sample journals",
        vector_config=Configure.Vectors.self_provided(),
        properties=[
            Property(name="name", data_type=DataType.TEXT),
            Property(name="issn", data_type=DataType.TEXT),
            Property(name="open_access", data_type=DataType.BOOL),
        ],
        references=[ReferenceProperty(name="editor", target_collection=AUTHOR)],
    )
    journals.data.insert_many(
        [
            {
                "name": f"Journal of {topic}",
                "issn": f"{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
                "open_access": rng.random() > 0.5,
            }
            for topic in ("Vectors", "Retrieval", "Graphs", "Databases", "NLP", "Systems")
        ]
    )

    articles = client.collections.create(
        ARTICLE,
        description="Sample articles with self-provided vectors",
        vector_config=Configure.Vectors.self_provided(),
        properties=[
            Property(name="title", data_type=DataType.TEXT),
            Property(name="body", data_type=DataType.TEXT),
            Property(name="year", data_type=DataType.INT),
            Property(name="rating", data_type=DataType.NUMBER),
            Property(name="published", data_type=DataType.BOOL),
            Property(name="published_at", data_type=DataType.DATE),
            Property(name="tags", data_type=DataType.TEXT_ARRAY),
        ],
        references=[
            ReferenceProperty(name="writtenBy", target_collection=AUTHOR),
            ReferenceProperty(name="cites", target_collection=ARTICLE),
            ReferenceProperty(name="publishedIn", target_collection=JOURNAL),
        ],
    )
    authors.config.add_reference(ReferenceProperty(name="favourite", target_collection=ARTICLE))
    start = datetime(2015, 1, 1, tzinfo=UTC)
    with articles.batch.fixed_size(batch_size=50) as batch:
        for i in range(120):
            batch.add_object(
                properties={
                    "title": f"{i:03d} " + sentence(rng, 4),
                    "body": " ".join(sentence(rng, rng.randint(8, 20)) for _ in range(3)),
                    "year": rng.randint(2015, 2026),
                    "rating": round(rng.uniform(0, 5), 2),
                    "published": rng.random() > 0.3,
                    "published_at": start + timedelta(days=rng.randint(0, 4000)),
                    "tags": rng.sample(TAGS, rng.randint(0, 3)),
                },
                vector=[rng.uniform(-1, 1) for _ in range(8)],
            )
    for alias, target in (("Articles", ARTICLE), ("Papers", ARTICLE), ("People", AUTHOR)):
        if client.alias.get(alias_name=alias) is None:
            client.alias.create(alias_name=alias, target_collection=target)
    if articles.batch.failed_objects:
        raise SystemExit(f"{len(articles.batch.failed_objects)} articles failed to insert")
    print(f"Created {AUTHOR} (15 objects), {JOURNAL} (6 objects) and {ARTICLE} (120 objects)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--grpc-port", type=int, default=50051)
    parser.add_argument("--reset", action="store_true", help="drop and recreate the collections")
    args = parser.parse_args()
    with weaviate.connect_to_local(args.host, args.port, args.grpc_port) as client:
        seed(client, args.reset)


if __name__ == "__main__":
    main()
