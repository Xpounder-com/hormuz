// External synthetic-preview verification. Never enumerate desktop UI or log titles/content.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Management;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;

public sealed class PreviewSample
{
    public string scenario;
    public int repetition;
    public double elapsed_seconds;
    public long working_set_bytes;
    public long private_bytes;
    public double process_cpu_seconds;
    public int process_count;
    public int handle_count;
    public uint gdi_objects;
    public uint user_objects;
}

public sealed class PreviewAcceptanceResult
{
    public string result = "passed";
    public string interaction_driver = "UI Automation InvokePattern/SetFocus and Win32 SendInput";
    public string keyboard_driver = "Win32 SendInput synthetic virtual-key events";
    public string reopen_driver = "synthetic NIN_SELECT notification to the owned window";
    public string hidden_behavior;
    public int completed_cycles;
    public List<string> checks = new List<string>();
    public List<PreviewSample> samples = new List<PreviewSample>();
    public double observer_cpu_seconds;
    public double observation_seconds;
    public int warmup_seconds;
    public int sample_count_per_scenario;
    public int repetitions;
    public int sample_interval_milliseconds;
}

public static class PreviewAcceptance
{
    const uint WmApp = 0x8000;
    const uint NinSelect = 0x400;
    const uint WmClose = 0x10;
    const uint KeyboardInputType = 1, KeyEventKeyUp = 2;
    const ushort VkTab = 0x09, VkReturn = 0x0D, VkShift = 0x10;
    const ushort VkEscape = 0x1B, VkSpace = 0x20;
    const int FoldId = 101, HideId = 102, ExitId = 104;
    const int TimeoutMilliseconds = 5000;

    [StructLayout(LayoutKind.Sequential)]
    struct Rect { public int Left, Top, Right, Bottom; }

    // The Win32 INPUT union must include MOUSEINPUT so cbSize matches the OS
    // structure on both 32-bit and 64-bit PowerShell processes.
    [StructLayout(LayoutKind.Sequential)]
    struct MouseInput
    {
        public int dx, dy;
        public uint mouseData, flags, time;
        public IntPtr extraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct KeyboardInput
    {
        public ushort virtualKey, scanCode;
        public uint flags, time;
        public IntPtr extraInfo;
    }

    [StructLayout(LayoutKind.Explicit)]
    struct InputUnion
    {
        [FieldOffset(0)] public MouseInput mouse;
        [FieldOffset(0)] public KeyboardInput keyboard;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct Input
    {
        public uint type;
        public InputUnion data;
    }

    [DllImport("user32.dll")]
    static extern IntPtr GetDlgItem(IntPtr window, int id);
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")]
    static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")]
    static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", SetLastError = true)]
    static extern uint GetGuiResources(IntPtr process, uint flags);
    [DllImport("user32.dll", SetLastError = true)]
    static extern IntPtr SendMessageTimeout(
        IntPtr window, uint message, UIntPtr wparam, IntPtr lparam,
        uint flags, uint timeout, out UIntPtr result);
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll", SetLastError = true)]
    static extern uint SendInput(uint count, Input[] inputs, int size);

    static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    static void Wait(Func<bool> condition, string message)
    {
        Stopwatch clock = Stopwatch.StartNew();
        do
        {
            if (condition()) return;
            Thread.Sleep(20);
        } while (clock.ElapsedMilliseconds < TimeoutMilliseconds);
        throw new InvalidOperationException(message);
    }

    static AutomationElement Button(IntPtr window, int id, string name)
    {
        IntPtr handle = GetDlgItem(window, id);
        Require(handle != IntPtr.Zero, "Expected native button is missing.");
        AutomationElement element = AutomationElement.FromHandle(handle);
        Require(element.Current.ControlType == ControlType.Button,
            "Expected UI Automation button role is missing.");
        Require(element.Current.Name == name, "UI Automation button name differs.");
        Require(element.Current.IsEnabled && element.Current.IsKeyboardFocusable,
            "UI Automation button must be enabled and keyboard focusable.");
        Require(!element.Current.IsOffscreen, "UI Automation button is offscreen.");
        return element;
    }

    static void Invoke(AutomationElement element)
    {
        object pattern;
        Require(element.TryGetCurrentPattern(InvokePattern.Pattern, out pattern),
            "UI Automation InvokePattern is unavailable.");
        ((InvokePattern)pattern).Invoke();
    }

    static int Height(IntPtr window)
    {
        Rect bounds;
        Require(GetWindowRect(window, out bounds), "Window bounds are unavailable.");
        return bounds.Bottom - bounds.Top;
    }

    static void Send(IntPtr window, uint message, uint notification)
    {
        UIntPtr result;
        Require(SendMessageTimeout(window, message, UIntPtr.Zero,
            new IntPtr(notification), 0x2, TimeoutMilliseconds, out result) != IntPtr.Zero,
            "Owned native window did not respond.");
    }

    static void Reopen(IntPtr window)
    {
        Send(window, WmApp + 1, NinSelect);
        Wait(() => IsWindowVisible(window) && !IsIconic(window), "Panel did not reopen.");
        IntPtr fold = GetDlgItem(window, FoldId);
        Wait(() => AutomationElement.FocusedElement != null &&
            AutomationElement.FocusedElement.Current.NativeWindowHandle == fold.ToInt32(),
            "Reopened panel did not restore button focus.");
    }

    static void Toggle(IntPtr window, string before, string after, bool shrinking)
    {
        int height = Height(window);
        Invoke(Button(window, FoldId, before));
        Wait(() => AutomationElement.FromHandle(GetDlgItem(window, FoldId)).Current.Name == after,
            "UI Automation invocation did not change fold state.");
        Require(shrinking ? Height(window) < height : Height(window) > height,
            "Fold state did not change native window height.");
    }

    static bool Hidden(IntPtr window)
    {
        return !IsWindowVisible(window) || IsIconic(window);
    }

    static void Hide(IntPtr window)
    {
        Invoke(Button(window, HideId, "Hide"));
        Wait(() => Hidden(window), "UI Automation Hide did not hide or minimize the panel.");
    }

    static bool Focused(IntPtr control)
    {
        AutomationElement focused = AutomationElement.FocusedElement;
        return focused != null && focused.Current.NativeWindowHandle == control.ToInt32();
    }

    static void RequireForeground(IntPtr window)
    {
        if (GetForegroundWindow() != window) SetForegroundWindow(window);
        Wait(() => GetForegroundWindow() == window,
            "Owned preview is not the foreground window; refusing to inject keyboard input.");
    }

    static Input KeyEvent(ushort virtualKey, bool release)
    {
        return new Input {
            type = KeyboardInputType,
            data = new InputUnion {
                keyboard = new KeyboardInput {
                    virtualKey = virtualKey,
                    flags = release ? KeyEventKeyUp : 0
                }
            }
        };
    }

    static void PressKey(IntPtr window, ushort virtualKey, bool shift)
    {
        Require(GetForegroundWindow() == window,
            "Owned preview lost foreground focus before keyboard input.");
        Input[] keys = shift
            ? new[] { KeyEvent(VkShift, false), KeyEvent(virtualKey, false),
                KeyEvent(virtualKey, true), KeyEvent(VkShift, true) }
            : new[] { KeyEvent(virtualKey, false), KeyEvent(virtualKey, true) };
        uint sent = SendInput((uint)keys.Length, keys, Marshal.SizeOf(typeof(Input)));
        if (sent != keys.Length)
        {
            // A partial Shift+Tab must not leave the CI desktop with Shift held.
            if (shift)
            {
                Input[] release = { KeyEvent(VkShift, true) };
                SendInput(1, release, Marshal.SizeOf(typeof(Input)));
            }
            throw new InvalidOperationException("Win32 SendInput did not inject the complete key sequence.");
        }
    }

    static void VerifyKeyboard(IntPtr window, PreviewAcceptanceResult report)
    {
        RequireForeground(window);
        IntPtr fold = GetDlgItem(window, FoldId);
        IntPtr hide = GetDlgItem(window, HideId);
        Button(window, FoldId, "Fold").SetFocus();
        Wait(() => Focused(fold), "Fold button could not receive keyboard focus.");

        PressKey(window, VkTab, false);
        Wait(() => Focused(hide), "Tab did not move focus from Fold to Hide.");
        PressKey(window, VkTab, true);
        Wait(() => Focused(fold), "Shift+Tab did not return focus to Fold.");
        report.checks.Add("sendinput_tab_shift_tab_focus_navigation");

        int fullHeight = Height(window);
        PressKey(window, VkSpace, false);
        Wait(() => AutomationElement.FromHandle(fold).Current.Name == "Expand",
            "Space did not activate Fold.");
        Require(Height(window) < fullHeight, "Space did not fold the native panel.");
        int foldedHeight = Height(window);
        PressKey(window, VkReturn, false);
        Wait(() => AutomationElement.FromHandle(fold).Current.Name == "Fold",
            "Enter did not activate Expand.");
        Require(Height(window) > foldedHeight, "Enter did not expand the native panel.");
        report.checks.Add("sendinput_space_enter_fold_expand");

        PressKey(window, VkEscape, false);
        Wait(() => Hidden(window), "Escape did not hide or minimize the panel.");
        Reopen(window);
        RequireForeground(window);
        Wait(() => Focused(fold), "Synthetic reopen did not restore keyboard focus to Fold.");
        PressKey(window, VkTab, false);
        Wait(() => Focused(hide), "Tab after reopen did not focus Hide.");
        PressKey(window, VkReturn, false);
        Wait(() => Hidden(window), "Enter did not activate Hide.");
        Reopen(window);
        RequireForeground(window);
        Wait(() => Focused(fold), "Second synthetic reopen did not restore keyboard focus.");
        report.checks.Add("sendinput_escape_enter_hide_and_reopen_focus");
    }

    // Query numeric process topology only. Do not collect command lines, paths or owners.
    // Each observation includes all currently discoverable descendants of this owned PID.
    static int ProcessCount(int root)
    {
        Dictionary<int, int> parents = new Dictionary<int, int>();
        using (ManagementObjectSearcher search = new ManagementObjectSearcher(
            "SELECT ProcessId, ParentProcessId FROM Win32_Process"))
        using (ManagementObjectCollection rows = search.Get())
        {
            foreach (ManagementObject row in rows)
            {
                using (row)
                    parents[Convert.ToInt32(row["ProcessId"])] = Convert.ToInt32(row["ParentProcessId"]);
            }
        }
        Require(parents.ContainsKey(root), "Owned preview process disappeared.");
        HashSet<int> found = new HashSet<int> { root };
        bool changed;
        do
        {
            changed = false;
            foreach (KeyValuePair<int, int> row in parents)
                if (found.Contains(row.Value) && found.Add(row.Key)) changed = true;
        } while (changed);
        return found.Count;
    }

    static PreviewSample Sample(Process process, string scenario, int repetition, Stopwatch clock)
    {
        int count = ProcessCount(process.Id);
        // This preview explicitly has no helpers. Do not report root-only metrics as a tree total.
        Require(count == 1, "Synthetic preview unexpectedly owns helper processes.");
        process.Refresh();
        uint gdi = GetGuiResources(process.Handle, 0);
        uint user = GetGuiResources(process.Handle, 1);
        Require(gdi > 0 && user > 0, "Native GUI resource counters are unavailable.");
        return new PreviewSample {
            scenario = scenario,
            repetition = repetition,
            elapsed_seconds = clock.Elapsed.TotalSeconds,
            working_set_bytes = process.WorkingSet64,
            private_bytes = process.PrivateMemorySize64,
            process_cpu_seconds = process.TotalProcessorTime.TotalSeconds,
            process_count = count,
            handle_count = process.HandleCount,
            gdi_objects = gdi,
            user_objects = user
        };
    }

    static void Observe(Process process, IntPtr window, PreviewAcceptanceResult report,
        string scenario, int repetition, Stopwatch clock)
    {
        Thread.Sleep(report.warmup_seconds * 1000);
        Stopwatch sampleClock = Stopwatch.StartNew();
        for (int index = 0; index < report.sample_count_per_scenario; index++)
        {
            long due = (long)index * report.sample_interval_milliseconds;
            long wait = due - sampleClock.ElapsedMilliseconds;
            if (wait > 0) Thread.Sleep((int)wait);
            Require(scenario == "hidden" ? Hidden(window) :
                IsWindowVisible(window) && !IsIconic(window), "Native scenario changed during sampling.");
            if (scenario != "hidden")
                Button(window, FoldId, scenario == "folded" ? "Expand" : "Fold");
            report.samples.Add(Sample(process, scenario, repetition, clock));
        }
    }

    public static PreviewAcceptanceResult Run(int processId, long expectedStartTicks,
        int warmupSeconds, int sampleCount, int intervalMilliseconds, int repetitions, int cycles)
    {
        using (Process process = Process.GetProcessById(processId))
        using (Process observer = Process.GetCurrentProcess())
        {
            Require(process.StartTime.ToUniversalTime().Ticks == expectedStartTicks,
                "Owned process identity changed before verification.");
            Stopwatch clock = Stopwatch.StartNew();
            double observerBefore = observer.TotalProcessorTime.TotalSeconds;
            IntPtr window = IntPtr.Zero;
            Wait(() => { process.Refresh(); window = process.MainWindowHandle;
                return window != IntPtr.Zero && IsWindowVisible(window); }, "Preview window is unavailable.");
            uint windowOwner;
            GetWindowThreadProcessId(window, out windowOwner);
            Require(windowOwner == processId, "Window does not belong to the owned preview.");

            PreviewAcceptanceResult report = new PreviewAcceptanceResult {
                warmup_seconds = warmupSeconds,
                sample_count_per_scenario = sampleCount,
                sample_interval_milliseconds = intervalMilliseconds,
                repetitions = repetitions
            };
            AutomationElement root = AutomationElement.FromHandle(window);
            Require(root.Current.Name == "Hormuz - synthetic preview", "Synthetic window label differs.");
            foreach (string label in new[] {
                "Synthetic data - no gateway connection", "Requests: 128 (synthetic)",
                "Tokens: 24,600 (synthetic)", "Est. cost: $0.42 (synthetic)" })
            {
                AutomationElement text = root.FindFirst(TreeScope.Descendants,
                    new PropertyCondition(AutomationElement.NameProperty, label));
                Require(text != null && !text.Current.IsOffscreen,
                    "Expected synthetic label is missing from the accessible tree.");
            }
            foreach (int id in new[] { FoldId, HideId, ExitId })
            {
                string name = id == FoldId ? "Fold" : id == HideId ? "Hide" : "Exit";
                AutomationElement button = Button(window, id, name);
                object pattern;
                Require(button.TryGetCurrentPattern(InvokePattern.Pattern, out pattern),
                    "Expected accessible action is unavailable.");
                button.SetFocus();
                Wait(() => button.Current.HasKeyboardFocus, "UI Automation could not focus a button.");
            }
            report.checks.Add("synthetic_accessible_labels_button_roles_names_actions_and_focus");

            Toggle(window, "Fold", "Expand", true);
            AutomationElement metric = root.FindFirst(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.NameProperty, "Requests: 128 (synthetic)"));
            Require(metric == null || metric.Current.IsOffscreen,
                "Folded synthetic metric remains exposed onscreen.");
            Toggle(window, "Expand", "Fold", false);
            report.checks.Add("uia_fold_expand_geometry_and_metric_visibility");

            Send(window, WmClose, 0);
            Wait(() => Hidden(window), "Panel close did not preserve a hidden resident window.");
            report.hidden_behavior = IsIconic(window) ? "taskbar_minimized_fallback" : "tray_hidden";
            Reopen(window);
            report.checks.Add("close_retains_resident_process_and_synthetic_reopen_restores_focus");

            VerifyKeyboard(window, report);

            for (int repetition = 1; repetition <= repetitions; repetition++)
            {
                Observe(process, window, report, "visible", repetition, clock);
                Toggle(window, "Fold", "Expand", true);
                Observe(process, window, report, "folded", repetition, clock);
                Hide(window);
                Observe(process, window, report, "hidden", repetition, clock);
                Reopen(window);
                Toggle(window, "Expand", "Fold", false);
            }

            Thread.Sleep(warmupSeconds * 1000);
            report.samples.Add(Sample(process, "before_cycles", 1, clock));
            for (int index = 0; index < cycles; index++)
            {
                Toggle(window, "Fold", "Expand", true);
                Toggle(window, "Expand", "Fold", false);
                Hide(window);
                Reopen(window);
                report.completed_cycles++;
            }
            Thread.Sleep(warmupSeconds * 1000);
            report.samples.Add(Sample(process, "after_cycles", 1, clock));
            report.checks.Add("repeated_uia_fold_expand_hide_and_synthetic_reopen");
            Invoke(Button(window, ExitId, "Exit"));
            Require(process.WaitForExit(TimeoutMilliseconds) && process.ExitCode == 0,
                "UI Automation Exit did not terminate the preview cleanly.");
            report.checks.Add("uia_exit_terminates_owned_process");
            observer.Refresh();
            report.observer_cpu_seconds = observer.TotalProcessorTime.TotalSeconds - observerBefore;
            report.observation_seconds = clock.Elapsed.TotalSeconds;
            return report;
        }
    }
}
