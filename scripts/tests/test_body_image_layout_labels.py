import unittest

from scripts.rag.generate_body_image_layout_labels import balanced_sample, summary


def candidate(dataset, digest, *, size="small", confidence="high", warning="none", image_only="no", suggestion="unknown"):
    return {
        "source_path": f"files/{dataset}/output/json/{digest}.json", "image_sha256": digest,
        "dataset": dataset, "suggested_layout_type": suggestion,
        "image_only_requires_ocr": image_only == "yes",
        "sampling_stratum": {"size": size, "confidence": confidence, "warnings": warning, "image_only": image_only},
    }


class BodyImageLayoutLabelsTests(unittest.TestCase):
    def test_sample_is_deterministic_and_deduplicated(self):
        items = [candidate("a", "1"), candidate("b", "2", size="large"), candidate("a", "1", confidence="low")]
        selected = balanced_sample(items, 3)
        self.assertEqual({item["image_sha256"] for item in selected}, {"1", "2"})

    def test_summary_has_required_dimensions(self):
        result = summary([candidate("a", "1", image_only="yes", suggestion="diagram")])
        self.assertEqual(result["dataset"], {"a": 1})
        self.assertEqual(result["suggested_layout_type"], {"diagram": 1})


if __name__ == "__main__":
    unittest.main()
