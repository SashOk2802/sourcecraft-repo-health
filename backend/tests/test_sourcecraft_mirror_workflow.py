import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "sourcecraft-mirror.yml"
DOCS = ROOT / "docs" / "sourcecraft-mirroring.md"


class SourceCraftMirrorWorkflowTests(unittest.TestCase):
    def test_mirrors_branches_and_tags_without_embedding_credentials(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("secrets.SOURCECRAFT_PAT", workflow)
        self.assertIn("GIT_ASKPASS", workflow)
        self.assertIn("GIT_TERMINAL_PROMPT=0", workflow)
        self.assertIn("+refs/heads/*:refs/heads/*", workflow)
        self.assertIn("+refs/tags/*:refs/tags/*", workflow)
        self.assertIn("push --force --prune", workflow)
        self.assertNotIn("https://${{ secrets.SOURCECRAFT_PAT }}", workflow)

    def test_has_automatic_and_manual_triggers(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("branches: [main]", workflow)
        self.assertNotIn("tags:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)

    def test_documents_source_of_truth_and_mirror_scope(self) -> None:
        docs = DOCS.read_text(encoding="utf-8")

        self.assertIn("GitHub — источник истины", docs)
        self.assertIn("SOURCECRAFT_PAT", docs)
        self.assertIn("Pull requests", docs)


if __name__ == "__main__":
    unittest.main()
