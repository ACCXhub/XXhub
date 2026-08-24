import xml.etree.ElementTree as ET


def test_uninstall_and_upgrade_removal_stop_tray_and_verified_service_before_files():
    namespace = {"w": "http://wixtoolset.org/schemas/v4/wxs"}
    root = ET.parse("packaging/wix/Product.wxs").getroot()

    action = root.find(
        ".//w:CustomAction[@Id='StopExistingAutoDyForRemove']",
        namespace,
    )
    assert action is not None
    assert "scripts\\autody-tray.ps1" in action.attrib["ExeCommand"]
    assert "-StopExisting" in action.attrib["ExeCommand"]
    assert action.attrib["Execute"] == "deferred"
    assert action.attrib["Impersonate"] == "yes"
    assert action.attrib["Return"] == "check"

    scheduled = root.find(
        ".//w:InstallExecuteSequence/w:Custom[@Action='StopExistingAutoDyForRemove']",
        namespace,
    )
    assert scheduled is not None
    assert scheduled.attrib["Before"] == "SetRemoveInstalledAutoDyTasksData"
    assert scheduled.attrib["Condition"] == 'REMOVE~="ALL"'
