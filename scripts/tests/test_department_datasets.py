"""Every registry department can be preprocessed, vectorized and loaded on its own."""

import unittest

from scripts.crawlers.common.storage import department_datasets, get_dataset_paths, get_dataset_root
from scripts.rag import load_to_supabase, preprocessing_pipeline, vectorization

ROOT = preprocessing_pipeline.PROJECT_ROOT


class DepartmentDatasetTests(unittest.TestCase):
    def test_ce_paths_and_tables_are_unchanged(self):
        web = preprocessing_pipeline.WEB_DATASETS["ce"]
        self.assertEqual(get_dataset_paths(ROOT, "ce").output, web.output_root)
        self.assertEqual(get_dataset_root(ROOT, "ce") / "preprocessed", web.preprocessed_root)
        self.assertEqual(get_dataset_root(ROOT, "ce") / "vectorized", vectorization.DATASET_PATHS["ce"]["output_root"])
        self.assertEqual("rag_sources", load_to_supabase.DATASET_TABLES["ce"]["sources"])

    def test_every_department_maps_to_the_department_tables(self):
        for name in department_datasets():
            with self.subTest(dataset=name):
                self.assertIn(name, preprocessing_pipeline.WEB_DATASETS)
                self.assertIn(name, vectorization.DATASET_PATHS)
                tables = load_to_supabase.DATASET_TABLES[name]
                self.assertEqual(("rag_sources", "rag_chunks"), (tables["sources"], tables["chunks"]))
                self.assertIn(name, preprocessing_pipeline.DATASET_CHOICES)

    def test_pilot_departments_use_their_registry_folders(self):
        me = preprocessing_pipeline.WEB_DATASETS["me"].output_root.relative_to(ROOT)
        self.assertEqual(("files", "department", "공과대학", "기계공학부", "기계학전공", "me", "output"), me.parts)

    def test_daily_all_is_still_the_four_datasets(self):
        self.assertEqual(["ce", "pknu_notice", "pknu_student_life", "rule"], preprocessing_pipeline.selected_datasets("all"))


if __name__ == "__main__":
    unittest.main()
