// HM-KNX DCA panel - a code-only WPF UserControl.

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Media;
using Knx.Ets.Common.Types.Enumerations;
using Knx.Ets.Sdk;
using Knx.Ets.Sdk.AddIns.AddInViews;
using Knx.Ets.Sdk.Network;
using Knx.Ets.Sdk.Project;

namespace HmKnx.Dca;

public sealed class DcaUserInterface : UserControl, IDisposable
{
    private readonly Project _project;
    private readonly Device _device;
    private readonly IInitializationContext _initializationContext;
    private readonly ObservableCollection<KoRow> _rows = new();

    // Cached DeviceManagement instance. ETS does ~10 s of PID=56 hardware-type
    // resolution on every fresh CreateDeviceManagement+Connect (the device
    // doesn't implement that PID, so ETS times out twice). By keeping a
    // single instance alive for the lifetime of the DCA panel we eat that
    // cost only once. Disposed when the DCA tab is closed.
    private DeviceManagement? _dmConnectionless;
    private DeviceManagement? _dmConnected;
    private readonly object _busLock = new();

    private readonly TextBlock _devicePaText = new() { Text = "-" };
    private readonly TextBlock _deviceVersionText = new() { Text = "-" };
    private readonly TextBlock _statusText = new() { Text = "bereit", Foreground = Brushes.DarkGreen };
    private readonly TextBox _logBox = new()
    {
        Height = 80, IsReadOnly = true, FontFamily = new FontFamily("Consolas"),
        FontSize = 11, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
    };

    private static readonly IReadOnlyDictionary<int, byte> DefaultFlags = new Dictionary<int, byte>
    {
        { 1,  Flag.Communicate | Flag.Write }, { 2,  Flag.Communicate | Flag.Write },
        { 3,  Flag.Communicate | Flag.Write }, { 4,  Flag.Communicate | Flag.Write },
        { 5,  Flag.Communicate | Flag.Read | Flag.Write | Flag.ReadOnInit },
        { 6,  Flag.Communicate | Flag.Read | Flag.Write | Flag.ReadOnInit },
        { 7,  Flag.Communicate | Flag.Read | Flag.Transmit },
        { 8,  Flag.Communicate | Flag.Read | Flag.Transmit },
        { 9,  Flag.Communicate | Flag.Read | Flag.Transmit },
        { 10, Flag.Communicate | Flag.Read | Flag.Transmit },
        { 11, Flag.Communicate | Flag.Read | Flag.Transmit },
        { 12, Flag.Communicate | Flag.Read | Flag.Transmit },
        { 13, Flag.Communicate | Flag.Read | Flag.Transmit },
        { 14, Flag.Communicate | Flag.Read | Flag.Transmit },
        { 15, Flag.Communicate | Flag.Read | Flag.Write | Flag.Transmit },
    };

    private static readonly (int Index, string Name, string Dpt, string Description)[] KoCatalog =
    {
        ( 1, "Open-Close",         "1.009",  "Open or close garage door"),
        ( 2, "Stop",                "1.010",  "Stop garage door if moving"),
        ( 3, "Vent",                "1.001",  "Open garage door for venting"),
        ( 4, "Light",               "1.012",  "Toggles the light"),
        ( 5, "Drive Lock",          "1.001",  "Locks the drive"),
        ( 6, "KNX Lock",            "1.001",  "Locks only the KNX-interface"),
        ( 7, "Status Open",         "1.002",  "1 if door is completely open"),
        ( 8, "Status Closed",       "1.002",  "1 if door is completely closed"),
        ( 9, "Status Venting",      "1.002",  "1 if door is at venting position"),
        (10, "Status Moving",       "1.002",  "1 if door is moving"),
        (11, "Status Moving Up",    "1.002",  "1 if door is moving up (S3)"),
        (12, "Status Moving Down",  "1.002",  "1 if door is moving down (S3)"),
        (13, "Status Pre-Warn",     "1.002",  "1 if door is about to move down (S3)"),
        (14, "Status Light",        "1.002",  "1 if light is switched on"),
        (15, "Status Error",        "12.000", "Error code, 0 if no error"),
    };

    public DcaUserInterface(Project project, Device device, IInitializationContext ctx)
    {
        _project = project;
        _device = device;
        _initializationContext = ctx;
        BuildUi();
        _devicePaText.Text = device?.Address?.ToString() ?? "-";
        _deviceVersionText.Text = "HM2KNX (15 KOs)";
        ReloadFromProject();
    }

    private void BuildUi()
    {
        var grid = new Grid { Margin = new Thickness(12) };
        for (int i = 0; i < 5; i++)
            grid.RowDefinitions.Add(new RowDefinition { Height = i == 3 ? new GridLength(1, GridUnitType.Star) : GridLength.Auto });

        // Row 0: Header
        var header = new TextBlock { Text = "HM-KNX (Hörmann Garagentor) - DCA",
                                     FontSize = 16, FontWeight = FontWeights.Bold };
        Grid.SetRow(header, 0);
        grid.Children.Add(header);

        // Row 1: Device info
        var infoGrid = new Grid { Margin = new Thickness(0, 8, 0, 8) };
        infoGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(120) });
        infoGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        for (int i = 0; i < 3; i++) infoGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        AddLabel(infoGrid, 0, 0, "Phys. Adresse:");
        AddLabel(infoGrid, 0, 1, _devicePaText);
        AddLabel(infoGrid, 1, 0, "Order/Version:");
        AddLabel(infoGrid, 1, 1, _deviceVersionText);
        AddLabel(infoGrid, 2, 0, "Status:");
        AddLabel(infoGrid, 2, 1, _statusText);
        Grid.SetRow(infoGrid, 1);
        grid.Children.Add(infoGrid);

        // Row 2: Buttons
        var btns = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 8) };
        btns.Children.Add(MakeButton("Identify", IdentifyButton_Click));
        btns.Children.Add(MakeButton("Read from Device", ReadButton_Click));
        btns.Children.Add(MakeButton("Write to Device", WriteButton_Click));
        btns.Children.Add(MakeButton("Restart", RestartButton_Click));
        btns.Children.Add(MakeButton("Toggle ProgMode", ProgmodeButton_Click));
        Grid.SetRow(btns, 2);
        grid.Children.Add(btns);

        // Row 3: KO grid
        var koGrid = new DataGrid
        {
            AutoGenerateColumns = false,
            IsReadOnly = true,
            HeadersVisibility = DataGridHeadersVisibility.Column,
            GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
            AlternatingRowBackground = Brushes.WhiteSmoke,
            ItemsSource = _rows,
        };
        // ElementStyle that wraps text and stretches each row vertically when
        // a KO has multiple GA entries (one per line).
        var wrapStyle = new Style(typeof(TextBlock));
        wrapStyle.Setters.Add(new Setter(TextBlock.TextWrappingProperty, TextWrapping.Wrap));
        wrapStyle.Setters.Add(new Setter(TextBlock.PaddingProperty, new Thickness(2)));

        koGrid.Columns.Add(new DataGridTextColumn { Header = "KO",    Binding = new Binding("Index"), Width = 35 });
        koGrid.Columns.Add(new DataGridTextColumn { Header = "Name",  Binding = new Binding("Name"),  Width = 140 });
        koGrid.Columns.Add(new DataGridTextColumn { Header = "DPT",   Binding = new Binding("Dpt"),   Width = 60 });
        koGrid.Columns.Add(new DataGridTextColumn { Header = "Flags", Binding = new Binding("Flags"), Width = 60 });
        koGrid.Columns.Add(new DataGridTextColumn
        {
            Header = "GAs",
            Binding = new Binding("GroupAddressList"),
            Width = new DataGridLength(1, DataGridLengthUnitType.Star),
            ElementStyle = wrapStyle,
        });
        koGrid.RowHeight = double.NaN;  // auto-fit per row content
        Grid.SetRow(koGrid, 3);
        grid.Children.Add(koGrid);

        // Row 4: Log
        Grid.SetRow(_logBox, 4);
        grid.Margin = new Thickness(0, 8, 0, 0);
        grid.Children.Add(_logBox);

        Content = grid;
    }

    private Button MakeButton(string text, RoutedEventHandler handler)
    {
        var b = new Button { Content = text, Padding = new Thickness(12, 6, 12, 6),
                             Margin = new Thickness(0, 0, 8, 0) };
        b.Click += handler;
        return b;
    }

    private static void AddLabel(Grid g, int row, int col, string text) =>
        AddLabel(g, row, col, new TextBlock { Text = text });

    private static void AddLabel(Grid g, int row, int col, TextBlock tb)
    {
        Grid.SetRow(tb, row);
        Grid.SetColumn(tb, col);
        g.Children.Add(tb);
    }

    private void ReloadFromProject()
    {
        _rows.Clear();
        foreach (var (idx, name, dpt, descr) in KoCatalog)
        {
            var gas = TryGetGroupAddressesForKo(idx);
            _rows.Add(new KoRow(idx, name, dpt, descr,
                DefaultFlags.TryGetValue(idx, out var b) ? b : (byte)0x0b, gas));
        }
    }

    private List<GaAssignment> TryGetGroupAddressesForKo(int koIndex)
    {
        var result = new List<GaAssignment>();
        try
        {
            foreach (ComObjectInstanceRef co in _device.ActiveComObjectInstanceRefs)
            {
                if (co.Number != (uint)koIndex) continue;
                foreach (Connector conn in co.Connectors)
                {
                    if (conn.GroupAddress != null)
                        result.Add(new GaAssignment(conn.GroupAddress.Address, conn.GroupAddress.Name ?? ""));
                }
            }
        }
        catch (Exception ex) { Log("ReloadFromProject (KO " + koIndex + "): " + ex.Message); }
        return result;
    }

    /// <summary>Get or open a cached DeviceManagement instance for the given
    /// transport mode. ETS' first Connect spends ~10 s on PID=56 hardware-type
    /// resolution that this firmware doesn't answer; caching makes that a
    /// one-time tax instead of paying it on every button click.</summary>
    private DeviceManagement OpenBus(bool connectionless = false)
    {
        lock (_busLock)
        {
            ref DeviceManagement? slot = ref connectionless ? ref _dmConnectionless : ref _dmConnected;
            if (slot == null)
            {
                var dm = ((DomObject)_project).Root.CreateDeviceManagement(_device, connectionless);
                if (dm == null) throw new InvalidOperationException("No bus connection available");
                dm.Connect();
                slot = dm;
            }
            return slot;
        }
    }

    public void Dispose()
    {
        lock (_busLock)
        {
            try { _dmConnected?.Disconnect(); } catch { }
            try { _dmConnectionless?.Disconnect(); } catch { }
            _dmConnected = null;
            _dmConnectionless = null;
        }
    }

    private async void IdentifyButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusOpAsync("Identifying...", "Identify", dm =>
        {
            // Read the 4 standard descriptor properties at fixed PIDs.
            // These all use KNX-spec start_index=1. Avoids the bonus
            // PropertyValue_Read+Memory_Read that ETS' GetDeviceDescriptor()
            // does internally and which run into T_NACK timeouts (16+ seconds).
            var mfg    = TryReadStdProp(dm, 0, 12, 2);   // PID_MANUFACTURER_ID
            var ser    = TryReadStdProp(dm, 0, 11, 6);   // PID_SERIAL_NUMBER
            var order  = TryReadStdProp(dm, 0, 15, 10);  // PID_ORDER_INFO (ASCII)
            var appVer = TryReadStdProp(dm, 3, 13, 5);   // PID_PROG_VERSION

            var sb = new StringBuilder();
            sb.AppendLine("Device descriptor:");
            if (mfg != null && mfg.Length >= 2)
                sb.AppendLine($"  Manufacturer: 0x{((mfg[0] << 8) | mfg[1]):x4}");
            if (ser != null) sb.AppendLine($"  Serial:       {BitConverter.ToString(ser).Replace('-', ':')}");
            if (order != null)
            {
                var ascii = System.Text.Encoding.ASCII.GetString(order).TrimEnd('\0');
                sb.AppendLine($"  Order:        \"{ascii}\"  ({BitConverter.ToString(order).Replace('-', ':')})");
            }
            if (appVer != null && appVer.Length >= 5)
                sb.AppendLine($"  App-Version:  {appVer[appVer.Length - 1]}  (raw {BitConverter.ToString(appVer).Replace('-', ':')})");
            return sb.ToString();
        }, connectionless: true);  // same T_NACK avoidance as Read
    }

    private static byte[]? TryReadStdProp(DeviceManagement dm, byte obj, byte pid, uint size)
    {
        try
        {
            return dm.ReadProperty(obj, pid, PropType.PDT_GENERIC_01,
                startElement: 1, count: 1, size: size);
        }
        catch { return null; }
    }

    /// <summary>Run a bus operation off the WPF UI thread. The op is a
    /// callback that receives an open DeviceManagement and returns a string
    /// to log on success. Disconnect + UI re-enable is handled centrally.</summary>
    private async Task RunBusOpAsync(string busyMsg, string opName,
        Func<DeviceManagement, string?> op, bool connectionless)
    {
        SetBusy(busyMsg);
        try
        {
            var result = await Task.Run(() =>
            {
                try
                {
                    var dm = OpenBus(connectionless);
                    return (true, op(dm), (Exception?)null);
                }
                catch (Exception ex) { return (false, (string?)null, ex); }
                // Note: no Disconnect in finally - DM stays cached. Disposed
                // when the user navigates away from the DCA tab.
            });

            if (result.Item3 != null) Fail(opName, result.Item3);
            else
            {
                if (!string.IsNullOrEmpty(result.Item2)) Log(result.Item2!);
                Status(opName + " OK", true);
            }
        }
        finally { SetIdle(); }
    }

    private void SetBusy(string msg)
    {
        _statusText.Text = msg;
        _statusText.Foreground = Brushes.Goldenrod;
        IsEnabled = false;
    }
    private void SetIdle() => IsEnabled = true;

    private async void ReadButton_Click(object sender, RoutedEventArgs e)
    {
        // Connectionless mode avoids ETS' strict TPCI stack, which rejects
        // this firmware's valid PropertyValue_Response frames.
        var newFlags = new Dictionary<int, byte>();
        await RunBusOpAsync("Reading...", "Read", dm =>
        {
            var probeKo1 = TryReadByte(dm, 11, 1);
            if (probeKo1 == null)
            {
                return "Read failed: device did not respond.\n"
                       + "This firmware's PropertyValue_Response frames are rejected by the\n"
                       + "ETS stack as out-of-sequence. Write should still work\n"
                       + "(no response parsing).";
            }
            newFlags[1] = probeKo1.Value;
            for (int ko = 2; ko <= 15; ko++)
            {
                var b = TryReadByte(dm, 11, (byte)ko);
                if (b != null) newFlags[ko] = b.Value;
            }
            var sb = new StringBuilder();
            sb.AppendLine("KO flags from device (IO 11):");
            for (int ko = 1; ko <= 15; ko++)
            {
                if (newFlags.TryGetValue(ko, out var b))
                    sb.AppendLine($"  KO {ko,2}: 0x{b:x2} ({Flag.ToString(b)})");
                else
                    sb.AppendLine($"  KO {ko,2}: <no response>");
            }
            return sb.ToString();
        }, connectionless: true);

        // Apply read flags back into the grid (must run on UI thread)
        foreach (var kv in newFlags)
        {
            var row = _rows.FirstOrDefault(r => r.Index == kv.Key);
            if (row != null) row.FlagsByte = kv.Value;
        }
        KoGridRefresh();
    }

    /// <summary>Read 1 byte from a homebrew property on IO 10/11. The HM-KNX
    /// firmware only honors wire start_index=0 here (start=1 elicits no
    /// response -> 5s ETS timeout). We skip the standard start=1 probe
    /// entirely to keep per-KO read time near bus latency.</summary>
    private static byte? TryReadByte(DeviceManagement dm, byte obj, byte pid)
    {
        try
        {
            var data = dm.ReadProperty(obj, pid, PropType.PDT_GENERIC_01,
                startElement: 0, count: 1, size: 1);
            if (data != null && data.Length >= 1) return data[0];
        }
        catch { /* swallow, return null */ }
        return null;
    }

    private void KoGridRefresh()
    {
        var snapshot = _rows.ToList();
        _rows.Clear();
        foreach (var r in snapshot) _rows.Add(r);
    }

    private async void WriteButton_Click(object sender, RoutedEventArgs e)
    {
        // Snapshot rows on UI thread (background thread mustn't touch them).
        var snap = _rows.Select(r => (r.Index, r.FlagsByte,
            GAs: r.GroupAddresses.Select(g => g.Address).ToList())).ToList();

        await RunBusOpAsync("Writing...", "Write", dm =>
        {
            int writes = 0;
            foreach (var row in snap)
            {
                dm.WriteProperty(11, (byte)row.Index, PropType.PDT_GENERIC_01,
                    startElement: 0, count: 1,
                    data: new[] { row.FlagsByte },
                    mask: new byte[] { 0xff },
                    writeOptions: WriteOptions.NoVerify);
                writes++;
            }
            byte slot = 1;
            foreach (var row in snap)
            {
                foreach (var ga in row.GAs)
                {
                    dm.WriteProperty(10, slot, PropType.PDT_GENERIC_03,
                        startElement: 0, count: 1,
                        data: new[] { (byte)(ga >> 8), (byte)(ga & 0xff), (byte)row.Index },
                        mask: new byte[] { 0xff, 0xff, 0xff },
                        writeOptions: WriteOptions.NoVerify);
                    writes++;
                    slot++;
                }
            }
            // End-marker
            dm.WriteProperty(10, slot, PropType.PDT_GENERIC_03,
                startElement: 0, count: 1,
                data: new byte[] { 0, 0, 0 },
                mask: new byte[] { 0xff, 0xff, 0xff },
                writeOptions: WriteOptions.NoVerify);
            writes++;
            return $"Wrote {writes} property entries.";
        }, connectionless: true);   // firmware echoes responses to writes too - same T_NACK avoidance as Read
    }

    private async void RestartButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusOpAsync("Restarting...", "Restart", dm =>
        {
            dm.Restart();
            return "Device restarted.";
        }, connectionless: false);
    }

    private async void ProgmodeButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusOpAsync("Toggling prog mode...", "ProgMode", dm =>
        {
            dm.WriteMemory(ResourceAddressSpace.StandardMemory, 0x60,
                data: new byte[] { 0x81 },
                mask: new byte[] { 0x81 },
                writeOptions: WriteOptions.NoVerify);
            return "ProgMode toggled (memory[0x60] ^= 0x81).";
        }, connectionless: false);
    }

    private void Log(string msg)
    {
        var ts = DateTime.Now.ToString("HH:mm:ss.fff");
        _logBox.AppendText(ts + "  " + msg + Environment.NewLine);
        _logBox.ScrollToEnd();
    }

    private void Status(string msg, bool ok)
    {
        _statusText.Text = msg;
        _statusText.Foreground = ok ? Brushes.DarkGreen : Brushes.DarkOrange;
    }

    private void Fail(string op, Exception ex)
    {
        Log(op + " FAILED: " + ex.GetType().Name + ": " + ex.Message);
        _statusText.Text = op + " failed";
        _statusText.Foreground = Brushes.Crimson;
    }
}

internal static class Flag
{
    public const byte Communicate = 0x01;
    public const byte Read        = 0x02;
    public const byte Write       = 0x04;
    public const byte Transmit    = 0x08;
    public const byte Update      = 0x20;
    public const byte ReadOnInit  = 0x40;

    public static string ToString(byte b)
    {
        var s = "";
        if ((b & Communicate) != 0) s += "C";
        if ((b & Read)        != 0) s += "R";
        if ((b & Write)       != 0) s += "W";
        if ((b & Transmit)    != 0) s += "T";
        if ((b & Update)      != 0) s += "U";
        if ((b & ReadOnInit)  != 0) s += "I";
        return s;
    }
}

public readonly struct GaAssignment
{
    public ushort Address { get; }
    public string Name { get; }
    public GaAssignment(ushort address, string name) { Address = address; Name = name; }
    public string Format() =>
        (Address >> 11) + "/" + ((Address >> 8) & 7) + "/" + (Address & 0xff)
        + (string.IsNullOrEmpty(Name) ? "" : " - " + Name);
}

public sealed class KoRow
{
    public int Index { get; }
    public string Name { get; }
    public string Dpt { get; }
    public string Description { get; }
    public byte FlagsByte { get; set; }
    public string Flags => Flag.ToString(FlagsByte);
    public List<GaAssignment> GroupAddresses { get; }
    public string GroupAddressList => string.Join("\n",
        GroupAddresses.Select(g => g.Format()));

    public KoRow(int index, string name, string dpt, string description,
                 byte flagsByte, List<GaAssignment> groupAddresses)
    {
        Index = index; Name = name; Dpt = dpt; Description = description;
        FlagsByte = flagsByte; GroupAddresses = groupAddresses;
    }
}
