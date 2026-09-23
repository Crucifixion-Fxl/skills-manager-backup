"""Build hooks for shipping the fixed-operation contracts in wheels."""

from pathlib import Path
from shutil import copy2

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent
CONTRACT_NAMES = tuple(path.name for path in sorted((ROOT / "contracts").glob("*.json")))


class build_py(_build_py):
    """Copy the repository snapshots into the installed package."""

    def run(self) -> None:
        super().run()
        destination = Path(self.build_lib) / "user_research" / "contracts"
        destination.mkdir(parents=True, exist_ok=True)
        for name in CONTRACT_NAMES:
            copy2(ROOT / "contracts" / name, destination / name)


setup(
    name="user-research-skill",
    version="0.2.0",
    description="Personal Agent Skill and fixed-operation Audience API client",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages("src"),
    include_package_data=True,
    package_data={"user_research": ["contracts/*.json"]},
    entry_points={"console_scripts": ["user-research-api=user_research.cli:main"]},
    cmdclass={"build_py": build_py},
)
