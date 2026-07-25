def test_package_importable():
    import devmcp

    assert devmcp.__version__ == "0.1.0"
