from autograder_b4.plugin import create_module


def test_descriptor_provides_platform_capabilities():
    module = create_module()
    assert module.descriptor.module == "B4"
    assert "b4-auth" in module.descriptor.provides
