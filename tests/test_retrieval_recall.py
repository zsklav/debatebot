import unittest

from tools.retrieval_recall import precision


def row(expected, predicted, overclaim=False):
    return {"criterion_id": "c", "expected": expected, "predicted": predicted, "overclaim": overclaim}


class PrecisionTests(unittest.TestCase):
    def test_counts_accepted_and_found_demonstrated(self):
        results = {"case": {"scores": {
            "reviewer_1": [row("demonstrated", "demonstrated"), row("not_assessed", "demonstrated", True)],
            "reviewer_2": [row("demonstrated", "needs_verification")],
            "council": None,
        }}}
        out = precision(results)
        self.assertEqual(out["reviewer_1"], {"said_demonstrated": 2, "label_accepts": 1,
                                             "labelled_demonstrated": 1, "found_demonstrated": 1})
        self.assertEqual(out["reviewer_2"]["found_demonstrated"], 0)
        self.assertEqual(out["council"]["said_demonstrated"], 0)


if __name__ == "__main__":
    unittest.main()
