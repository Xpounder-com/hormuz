// Content-free observer for the owned synthetic Windows preview only.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Management;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;

public sealed class FootprintSample
{
    public double elapsed_seconds;
    public int process_count;
    public long working_set_bytes_sum;
    public long private_bytes_sum;
    public double process_cpu_seconds_sum;
    public int root_handle_count;
}

public sealed class FootprintRun
{
    public string result = "passed";
    public string scenario;
    public int repetition;
    public int warmup_seconds;
    public int duration_seconds_requested;
    public int sample_interval_milliseconds;
    public double window_ready_upper_bound_seconds;
    public string hidden_behavior;
    public List<FootprintSample> samples = new List<FootprintSample>();
    public double sample_duration_seconds;
    public long working_set_bytes_min;
    public long working_set_bytes_max;
    public long private_bytes_min;
    public long private_bytes_max;
    public double app_cpu_seconds_delta;
    public double app_cpu_percent_one_core;
    public double observer_cpu_seconds_sampling;
    public double observer_wall_seconds_sampling;
    public long observer_peak_working_set_bytes;
    public object process_wakeups = null;
    public string process_wakeups_state = "unavailable: hosted runner has no verified per-process wake-up counter";
}

public static class FootprintBaseline
{
    const int FoldId = 101, HideId = 102, ExitId = 104;
    const uint WmApp = 0x8000, NinSelect = 0x400;

    [DllImport("user32.dll")]
    static extern IntPtr GetDlgItem(IntPtr window, int id);
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")]
    static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll", SetLastError = true)]
    static extern IntPtr SendMessageTimeout(IntPtr window, uint message, UIntPtr wparam,
        IntPtr lparam, uint flags, uint timeout, out UIntPtr result);
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

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
            Thread.Sleep(25);
        } while (clock.ElapsedMilliseconds < 10000);
        throw new InvalidOperationException(message);
    }

    static bool Hidden(IntPtr window)
    {
        return !IsWindowVisible(window) || IsIconic(window);
    }

    static AutomationElement Button(IntPtr window, int id, string name)
    {
        IntPtr handle = GetDlgItem(window, id);
        Require(handle != IntPtr.Zero, "Owned preview button is missing.");
        AutomationElement element = AutomationElement.FromHandle(handle);
        Require(element != null && element.Current.ControlType == ControlType.Button &&
            element.Current.Name == name && element.Current.IsEnabled,
            "Owned preview button contract changed.");
        return element;
    }

    static void Invoke(AutomationElement element)
    {
        object pattern;
        Require(element.TryGetCurrentPattern(InvokePattern.Pattern, out pattern),
            "Owned preview button has no UI Automation action.");
        ((InvokePattern)pattern).Invoke();
    }

    static HashSet<int> ProcessTree(int root)
    {
        // Keep only numeric PID topology in memory; never read arguments or owners.
        Dictionary<int, int> parents = new Dictionary<int, int>();
        using (ManagementObjectSearcher search = new ManagementObjectSearcher(
            "SELECT ProcessId, ParentProcessId FROM Win32_Process"))
        using (ManagementObjectCollection rows = search.Get())
        {
            foreach (ManagementObject row in rows)
            {
                using (row)
                    parents[Convert.ToInt32(row["ProcessId"])] =
                        Convert.ToInt32(row["ParentProcessId"]);
            }
        }
        Require(parents.ContainsKey(root), "Owned preview disappeared from process topology.");
        HashSet<int> found = new HashSet<int> { root };
        bool changed;
        do
        {
            changed = false;
            foreach (KeyValuePair<int, int> row in parents)
                if (found.Contains(row.Value) && found.Add(row.Key)) changed = true;
        } while (changed);
        return found;
    }

    static FootprintSample Sample(Process root, Stopwatch clock)
    {
        HashSet<int> tree = ProcessTree(root.Id);
        // The empty preview is expected to own no helper. Fail instead of publishing
        // a root-only aggregate if that invariant changes.
        Require(tree.Count == 1, "Synthetic preview unexpectedly owns a helper process.");
        long workingSet = 0, privateBytes = 0;
        double cpu = 0;
        int handles = 0;
        foreach (int pid in tree)
        {
            using (Process process = Process.GetProcessById(pid))
            {
                process.Refresh();
                workingSet += process.WorkingSet64;
                privateBytes += process.PrivateMemorySize64;
                cpu += process.TotalProcessorTime.TotalSeconds;
                if (pid == root.Id) handles = process.HandleCount;
            }
        }
        return new FootprintSample {
            elapsed_seconds = clock.Elapsed.TotalSeconds,
            process_count = tree.Count,
            working_set_bytes_sum = workingSet,
            private_bytes_sum = privateBytes,
            process_cpu_seconds_sum = cpu,
            root_handle_count = handles
        };
    }

    static void RequireState(IntPtr window, string scenario)
    {
        if (scenario == "hidden")
            Require(Hidden(window), "Hidden preview became visible during the sample.");
        else
        {
            Require(IsWindowVisible(window) && !IsIconic(window),
                "Visible preview changed state during the sample.");
            Button(window, FoldId, scenario == "folded" ? "Expand" : "Fold");
        }
    }

    public static FootprintRun Run(int processId, long expectedStartTicks,
        long launchUtcTicks, string scenario, int repetition, int warmupSeconds,
        int durationSeconds, int intervalMilliseconds)
    {
        Require(scenario == "visible" || scenario == "folded" || scenario == "hidden",
            "Unknown synthetic scenario.");
        Require(repetition >= 1 && repetition <= 3 && warmupSeconds >= 0 &&
            durationSeconds > 0 && intervalMilliseconds > 0 &&
            durationSeconds * 1000 % intervalMilliseconds == 0,
            "Invalid bounded observation parameters.");
        using (Process process = Process.GetProcessById(processId))
        using (Process observer = Process.GetCurrentProcess())
        {
            Require(process.StartTime.ToUniversalTime().Ticks == expectedStartTicks,
                "Owned preview process identity changed.");
            IntPtr window = IntPtr.Zero;
            Wait(() => { process.Refresh(); window = process.MainWindowHandle;
                return window != IntPtr.Zero && IsWindowVisible(window); },
                "Owned preview window did not become visible.");
            uint owner;
            GetWindowThreadProcessId(window, out owner);
            Require(owner == processId, "Observed window is not owned by the preview.");
            AutomationElement root = AutomationElement.FromHandle(window);
            Require(root != null && root.Current.Name == "Hormuz - synthetic preview",
                "Observed window is not the synthetic preview.");
            Button(window, FoldId, "Fold");
            Button(window, HideId, "Hide");
            Button(window, ExitId, "Exit");

            FootprintRun report = new FootprintRun {
                scenario = scenario,
                repetition = repetition,
                warmup_seconds = warmupSeconds,
                duration_seconds_requested = durationSeconds,
                sample_interval_milliseconds = intervalMilliseconds,
                // Worker startup and UIA polling are included: this is an upper
                // bound on observed window readiness, not a frame or cold start.
                window_ready_upper_bound_seconds =
                    (DateTime.UtcNow.Ticks - launchUtcTicks) / 10000000.0
            };
            Require(report.window_ready_upper_bound_seconds >= 0,
                "Launch timestamp is later than observed readiness.");
            if (scenario == "folded")
            {
                Invoke(Button(window, FoldId, "Fold"));
                Wait(() => AutomationElement.FromHandle(GetDlgItem(window, FoldId)).Current.Name == "Expand",
                    "Preview did not fold.");
            }
            else if (scenario == "hidden")
            {
                Invoke(Button(window, HideId, "Hide"));
                Wait(() => Hidden(window), "Preview did not hide.");
                report.hidden_behavior = IsIconic(window)
                    ? "taskbar_minimized_fallback" : "tray_hidden";
            }
            RequireState(window, scenario);
            Thread.Sleep(warmupSeconds * 1000);

            int count = durationSeconds * 1000 / intervalMilliseconds + 1;
            double observerBefore = observer.TotalProcessorTime.TotalSeconds;
            Stopwatch clock = Stopwatch.StartNew();
            for (int index = 0; index < count; index++)
            {
                long due = (long)index * intervalMilliseconds;
                long wait = due - clock.ElapsedMilliseconds;
                if (wait > 0) Thread.Sleep((int)wait);
                RequireState(window, scenario);
                report.samples.Add(Sample(process, clock));
            }
            report.observer_wall_seconds_sampling = clock.Elapsed.TotalSeconds;
            observer.Refresh();
            report.observer_cpu_seconds_sampling =
                observer.TotalProcessorTime.TotalSeconds - observerBefore;
            report.observer_peak_working_set_bytes = observer.PeakWorkingSet64;
            report.sample_duration_seconds =
                report.samples[count - 1].elapsed_seconds - report.samples[0].elapsed_seconds;
            Require(report.sample_duration_seconds >= durationSeconds - 1,
                "Sample interval was shorter than the requested duration.");
            report.working_set_bytes_min = long.MaxValue;
            report.private_bytes_min = long.MaxValue;
            foreach (FootprintSample sample in report.samples)
            {
                report.working_set_bytes_min = Math.Min(report.working_set_bytes_min,
                    sample.working_set_bytes_sum);
                report.working_set_bytes_max = Math.Max(report.working_set_bytes_max,
                    sample.working_set_bytes_sum);
                report.private_bytes_min = Math.Min(report.private_bytes_min,
                    sample.private_bytes_sum);
                report.private_bytes_max = Math.Max(report.private_bytes_max,
                    sample.private_bytes_sum);
            }
            report.app_cpu_seconds_delta =
                report.samples[count - 1].process_cpu_seconds_sum -
                report.samples[0].process_cpu_seconds_sum;
            Require(report.app_cpu_seconds_delta >= 0,
                "App CPU counter regressed during the sample.");
            report.app_cpu_percent_one_core =
                100 * report.app_cpu_seconds_delta / report.sample_duration_seconds;

            if (Hidden(window))
            {
                UIntPtr ignored;
                Require(SendMessageTimeout(window, WmApp + 1, UIntPtr.Zero,
                    new IntPtr((int)NinSelect), 0x2, 5000, out ignored) != IntPtr.Zero,
                    "Owned preview did not accept synthetic reopen.");
                Wait(() => IsWindowVisible(window) && !IsIconic(window),
                    "Owned preview did not reopen.");
            }
            Invoke(Button(window, ExitId, "Exit"));
            Require(process.WaitForExit(10000) && process.ExitCode == 0,
                "Owned preview did not exit cleanly.");
            return report;
        }
    }
}
