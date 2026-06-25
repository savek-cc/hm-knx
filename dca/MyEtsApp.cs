// HM-KNX ETS6 DCA — entry point.
//
// Uses the modern AddIn pattern (System.AddIn + IDeviceConfigurationAddIn),
// not the deprecated MultiPassDownloadPluginBase. ETS6 instantiates this
// class when the user activates the DCA tab on a device whose
// ApplicationProgram Id is listed in AddInManifest.xml.
//
// The UI is built in code in DcaUserInterface (WPF UserControl).

using System;
using System.AddIn;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Xml;
using Knx.Ets.Sdk;
using Knx.Ets.Sdk.AddIns.AddInViews;
using Knx.Ets.Sdk.Project;

namespace HmKnx.Dca;

[AddIn("HM-KNX.Dca", Version = "0.1.0", Publisher = "Community")]
public sealed class MyEtsApp : IDeviceConfigurationAddIn, IEts4AddInV2, IEts4AddIn, IDisposable
{
    private Project? _project;
    private IInitializationContext? _initializationContext;

    /// <summary>
    /// AppId must match the AppId in AddInManifest.xml. ETS uses it to
    /// route DCA activations to this AddIn.
    /// </summary>
    public string AppId => "M00FA-A4805";

    public XmlDocument? Configuration => null;
    public XmlDocument? UserConfiguration => null;

    public void Initialize(IInitializationContext initializationContext)
    {
        _project = initializationContext.Project;
        _initializationContext = initializationContext;
    }

    public void Dispose() { }

    public void OnProjectOpened(Project openedProject) { }
    public void OnProjectClosing(Project closingProject, bool changesSinceProjectOpened) { }
    public void Ets4SelectionChanged(IEnumerable<DomObject> selectedObjects) { }
    public void ToolbarItemClick(string itemIdentifier) { }
    public bool ShowConfigurationDialog(XmlDocument configuration, XmlDocument userConfiguration) => false;

    public FrameworkElement? GetAddInUI() => null;
    public FrameworkElement? GetSidebarControl(IEnumerable<object> selectedObjects) => null;
    public FrameworkElement? GetSidebarProperties(IEnumerable<object> selectedObjects) => null;

    /// <summary>
    /// Called when the user opens the DCA tab on a HM-KNX device.
    /// Returns the WPF UserControl that becomes the DCA panel content.
    /// </summary>
    public FrameworkElement? GetDcaUi(IEnumerable<Device> devices)
    {
        var deviceList = devices?.ToList() ?? new List<Device>();
        if (deviceList.Count != 1 || _project is null || _initializationContext is null)
        {
            return null;
        }
        return new DcaUserInterface(_project, deviceList[0], _initializationContext);
    }
}
