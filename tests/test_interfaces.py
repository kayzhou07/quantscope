import unittest

try:
    from quantscope.interfaces.http import dispatch
except ImportError:
    dispatch = None


class Interfaces(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(dispatch, "统一HTTP接口尚未实现")

    def test_models_and_health(self):
        code, d = dispatch("GET", "/api/v1/models", None)
        self.assertEqual(code, 200)
        self.assertEqual(len(d["models"]), 4)
        self.assertEqual(dispatch("GET", "/health", None)[0], 200)

    def test_missing_model_rejected(self):
        self.assertEqual(dispatch("POST", "/api/v1/signals", {})[0], 400)

    def test_unknown_route(self):
        self.assertEqual(dispatch("GET", "/unknown", None)[0], 404)
