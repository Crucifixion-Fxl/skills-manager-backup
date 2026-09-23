"""Build hook for shipping the frozen contract files in wheels."""

from pathlib import Path
from shutil import copy2

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent
CONTRACT_NAMES = (
    "audience-sync-v2.openapi.json",
    "operation-registry.json",
    "source.json",
    "project-control-plane.openapi.json",
    "project-operation-registry.json",
)


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        destination = Path(self.build_lib) / "audience_sync" / "contracts"
        destination.mkdir(parents=True, exist_ok=True)
        for name in CONTRACT_NAMES:
            copy2(ROOT / "contracts" / name, destination / name)


setup(
    name="audience-sync-skill",
    version="0.8.1",
    description="Audience Sync v2 Personal Agent Skill and fixed-operation client",
    python_requires=">=3.9",
    entry_points={"console_scripts": ["audience-sync-api=audience_sync.cli:main"]},
    cmdclass={"build_py": build_py},
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"audience_sync": ["py.typed"]},
)
