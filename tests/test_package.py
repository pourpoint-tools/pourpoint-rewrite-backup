from pathlib import Path

import pourpoint


def test_package_imports_from_python_source():
    assert pourpoint.__file__ is not None
    package_path = Path(pourpoint.__file__)
    assert package_path.name == "__init__.py"
    assert package_path.parent.name == "pourpoint"
    assert package_path.with_name("py.typed").is_file()
