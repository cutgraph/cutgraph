using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;

internal static class Native
{
    [StructLayout(LayoutKind.Sequential)] internal struct StartupInfo
    {
        internal int cb; internal string reserved, desktop, title;
        internal int x, y, width, height, xChars, yChars, fill, flags;
        internal short show, reservedSize; internal IntPtr reservedData, input, output, error;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct ProcessInfo
    { internal IntPtr process, thread; internal int pid, tid; }
    [StructLayout(LayoutKind.Sequential)] internal struct BasicLimit
    {
        internal long processTime, jobTime; internal uint flags;
        internal UIntPtr minWorking, maxWorking; internal uint activeProcesses;
        internal UIntPtr affinity; internal uint priority, scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct IoCounters
    { internal ulong read, write, other, readBytes, writeBytes, otherBytes; }
    [StructLayout(LayoutKind.Sequential)] internal struct ExtendedLimit
    {
        internal BasicLimit basic; internal IoCounters io;
        internal UIntPtr processMemory, jobMemory, peakProcessMemory, peakJobMemory;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct Accounting
    {
        internal long user, kernel, periodUser, periodKernel;
        internal uint faults, total, active, terminated;
    }
    internal delegate bool WindowCallback(IntPtr window, IntPtr arg);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern IntPtr CreateJobObject(IntPtr security, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimit limits, int size);
    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool QueryInformationJobObject(IntPtr job, int kind, out Accounting info, int size, IntPtr length);
    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool QueryInformationJobObject(IntPtr job, int kind, IntPtr info, int size, IntPtr length);
    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern bool CreateProcess(string app, StringBuilder command, IntPtr processSecurity,
        IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string cwd,
        ref StartupInfo startup, out ProcessInfo process);
    [DllImport("kernel32.dll")] internal static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll")] internal static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")] internal static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll")] internal static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
    [DllImport("user32.dll")] internal static extern bool EnumWindows(WindowCallback callback, IntPtr arg);
    [DllImport("user32.dll")] internal static extern uint GetWindowThreadProcessId(IntPtr window, out int pid);
    [DllImport("user32.dll")] internal static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] internal static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] internal static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] internal static extern bool AllowSetForegroundWindow(int pid);
    [DllImport("user32.dll")] internal static extern bool PostMessage(IntPtr window, uint message, IntPtr w, IntPtr l);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    internal static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    [DllImport("iphlpapi.dll")] internal static extern uint GetExtendedTcpTable(
        IntPtr table, ref int size, bool order, int family, int tableClass, uint reserved);

    internal static void Check(bool ok)
    { if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error()); }
}

internal sealed class ProcessJob : IDisposable
{
    internal IntPtr Handle;
    readonly Dictionary<int, IntPtr> processes = new Dictionary<int, IntPtr>();
    internal ProcessJob()
    {
        Handle = Native.CreateJobObject(IntPtr.Zero, null);
        Native.Check(Handle != IntPtr.Zero);
        var limits = new Native.ExtendedLimit();
        // The launcher owns future verified installer spawning, outside this job.
        limits.basic.flags = 0x2000;
        try { Native.Check(Native.SetInformationJobObject(Handle, 9, ref limits, Marshal.SizeOf(limits))); }
        catch { Dispose(); throw; }
    }
    internal int Start(string executable, string[] arguments, string cwd)
    {
        var startup = new Native.StartupInfo();
        startup.cb = Marshal.SizeOf(startup);
        var command = new StringBuilder(Quote(executable));
        foreach (string arg in arguments) command.Append(" ").Append(Quote(arg));
        Native.ProcessInfo info;
        Native.Check(Native.CreateProcess(executable, command, IntPtr.Zero, IntPtr.Zero, false,
            0x08000004, IntPtr.Zero, cwd, ref startup, out info)); // Suspended, without console.
        try
        {
            Native.Check(Native.AssignProcessToJobObject(Handle, info.process));
            Native.Check(Native.ResumeThread(info.thread) != UInt32.MaxValue);
            processes.Add(info.pid, info.process);
            return info.pid;
        }
        catch { Native.TerminateProcess(info.process, 1); Native.CloseHandle(info.process); throw; }
        finally { Native.CloseHandle(info.thread); }
    }
    internal static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(ch); slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }
    internal bool Alive(int pid)
    { return processes.ContainsKey(pid) && Native.WaitForSingleObject(processes[pid], 0) == 258; }
    internal HashSet<int> ProcessIds()
    {
        for (int size = 4096; size <= 1024 * 1024; size *= 2)
        {
            IntPtr memory = Marshal.AllocHGlobal(size);
            try
            {
                if (!Native.QueryInformationJobObject(Handle, 3, memory, size, IntPtr.Zero)) continue;
                int count = Marshal.ReadInt32(memory, 4);
                var result = new HashSet<int>();
                for (int i = 0; i < count; i++) result.Add((int)Marshal.ReadIntPtr(memory, 8 + i * IntPtr.Size));
                return result;
            }
            finally { Marshal.FreeHGlobal(memory); }
        }
        throw new InvalidOperationException("Studio process list is unavailable.");
    }
    internal uint Active
    {
        get
        {
            Native.Accounting info;
            Native.Check(Native.QueryInformationJobObject(Handle, 1, out info,
                Marshal.SizeOf(typeof(Native.Accounting)), IntPtr.Zero));
            return info.active;
        }
    }
    internal void Stop()
    {
        Native.Check(Native.TerminateJobObject(Handle, 0));
        var until = DateTime.UtcNow.AddSeconds(15);
        while (Active != 0 && DateTime.UtcNow < until) System.Threading.Thread.Sleep(50);
        if (Active != 0) throw new InvalidOperationException("Studio processes did not stop in time.");
    }
    public void Dispose()
    {
        if (Handle != IntPtr.Zero) { Native.CloseHandle(Handle); Handle = IntPtr.Zero; }
        foreach (IntPtr process in processes.Values) Native.CloseHandle(process);
        processes.Clear();
    }
}
