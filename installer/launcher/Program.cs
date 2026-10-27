using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Cutgraph Studio")]
[assembly: System.Reflection.AssemblyProduct("Cutgraph Studio")]

internal static class Program
{
    internal const string AppId = "Cutgraph.Studio";
    static string state, log;
    static bool headless, quiet;
    static Update pendingUpdate;
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

    [STAThread] static int Main(string[] args)
    {
        headless = Array.IndexOf(args, "--headless") >= 0;
        bool checkPath = args.Length == 2 && args[0] == "--check-install-path";
        bool clearProgram = args.Length == 2 && args[0] == "--clear-program-files";
        bool verifyUpdate = args.Length == 1 && args[0] == "--verify-update";
        bool requestUpdate = args.Length == 1 && args[0] == "--request-update";
        bool pathMode = checkPath || clearProgram;
        quiet = headless || pathMode || verifyUpdate || requestUpdate || Array.IndexOf(args, "--stop") >= 0 || Array.IndexOf(args, "--remove-addons") >= 0;
        try
        {
            foreach (string arg in pathMode ? new string[0] : args)
                if (arg != "--stop" && arg != "--headless" && arg != "--remove-addons" && arg != "--verify-update" && arg != "--request-update") throw new ArgumentException("Unknown launcher option.");
            state = Environment.GetEnvironmentVariable("CUTGRAPH_STATE_HOME");
            if (String.IsNullOrWhiteSpace(state)) state = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Cutgraph Studio");
            state = state.Trim();
            if (!DataPaths.Absolute(state))
            { if (pathMode) return 2; throw new ArgumentException("CUTGRAPH_STATE_HOME must be an absolute path."); }
            try { state = Path.GetFullPath(state); }
            catch (ArgumentException) { if (pathMode) return 2; throw; }
            if (pathMode && !DataPaths.Absolute(args[1])) throw new ArgumentException("The application folder must be an absolute path.");
            if (DataPaths.Overlaps(state, pathMode ? args[1] : AppDomain.CurrentDomain.BaseDirectory))
            { if (pathMode) return 3; throw new ArgumentException("The Studio data folder must be separate from the application folder."); }
            if (checkPath) { DataPaths.CheckTree(args[1]); return 0; }
            if (clearProgram) { DataPaths.ClearProgramFiles(args[1]); return 0; }
            Directory.CreateDirectory(Path.Combine(state, "runtime"));
            log = Path.Combine(state, "runtime", "launcher.log");
            if (verifyUpdate) { using (Update.Verify(state)) return 0; }
            if (Array.IndexOf(args, "--remove-addons") >= 0)
            { DataPaths.RemoveAddons(state, AppDomain.CurrentDomain.BaseDirectory); Log("Downloaded add-ons removed."); return 0; }
            // Installation identity deliberately ignores the data-folder override used by Setup.
            using (var objects = new SessionObjects(AppDomain.CurrentDomain.BaseDirectory))
            {
                var mutex = objects.Mutex;
                if (requestUpdate) return Update.Request(state, objects);
                if (Array.IndexOf(args, "--stop") >= 0) return StopExisting(objects);
                if (!ActivateOrAcquire(objects)) return 0;
                try
                {
                    Environment.SetEnvironmentVariable("CUTGRAPH_STATE_HOME", state);
                    Environment.SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1");
                    Environment.SetEnvironmentVariable("PYTHONNOUSERSITE", "1");
                    Native.SetCurrentProcessExplicitAppUserModelID(AppId);
                    using (var stop = objects.CreateEvent("stop"))
                    using (var update = objects.CreateEvent("update"))
                    using (var job = new ProcessJob())
                    {
                        try
                        {
                            using (var activate = objects.CreateEvent("activate"))
                            { objects.PublishOwner(); Run(job, stop, activate, update); }
                        }
                        finally
                        {
                            Log("Closing; activation will start a new instance.");
                            try { CloseWindows(job); }
                            finally { job.Stop(); }
                            File.Delete(Path.Combine(state, "runtime", "launcher.json"));
                            Log("Stopped; owned process tree is empty.");
                        }
                    }
                }
                finally { objects.UnpublishOwner(); mutex.ReleaseMutex(); }
            }
            if (pendingUpdate != null)
                using (pendingUpdate) pendingUpdate.Launch();
            return 0;
        }
        catch (DataPaths.LinkedPathException error)
        { Log(error.ToString()); return pathMode ? 4 : 1; }
        catch (Exception error)
        {
            Log(error.ToString());
            if (!quiet) MessageBox.Show("Cutgraph Studio could not continue. " + error.Message +
                "\n\nClose Studio and try again. Details are in runtime\\launcher.log in your data folder.",
                "Cutgraph Studio", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
    static bool Acquire(Mutex mutex, int timeout)
    { try { return mutex.WaitOne(timeout); } catch (AbandonedMutexException) { return true; } }
    static bool ActivateOrAcquire(SessionObjects objects)
    {
        var clock = Stopwatch.StartNew();
        while (clock.Elapsed.TotalSeconds < 25)
        {
            if (Acquire(objects.Mutex, 50)) return true;
            using (var owner = objects.TryOpenOwner())
            {
                if (owner == null) continue;
                using (var activate = objects.TryOpenEvent("activate", owner))
                {
                    if (activate == null) { owner.WaitForExit(50); continue; }
                    Native.AllowSetForegroundWindow(owner.Id);
                    activate.Set();
                    // Acknowledgment comes only from the running UI loop. A closing owner
                    // leaves the request pending so this click can acquire the next instance.
                    while (clock.Elapsed.TotalSeconds < 25 && !owner.WaitForExit(50))
                        if (!activate.WaitOne(0)) return false;
                }
            }
        }
        throw new IOException("The running Studio is not responding. Close it and try again.");
    }
    static int StopExisting(SessionObjects objects)
    {
        var clock = Stopwatch.StartNew();
        while (clock.Elapsed.TotalSeconds < 30)
        {
            if (Acquire(objects.Mutex, 50)) { objects.Mutex.ReleaseMutex(); return 0; }
            using (var owner = objects.TryOpenOwner())
            {
                if (owner == null) continue;
                using (var stop = objects.TryOpenEvent("stop", owner))
                    if (stop != null) stop.Set();
                // Keep the verified process handle: reopening the mapping could stop
                // a replacement instance after this one releases its mutex.
                if (!owner.WaitForExit(Math.Max(1, 30000 - (int)clock.ElapsedMilliseconds))) break;
                Log("Stop completed; the launcher process has exited.");
                return 0;
            }
        }
        throw new InvalidOperationException("Studio could not stop. Close it before continuing.");
    }
    internal static void Log(string message)
    {
        if (log == null) return;
        try
        {
            if (File.Exists(log) && new FileInfo(log).Length > 2 * 1024 * 1024)
            { File.Copy(log, log + ".old", true); File.Delete(log); }
            File.AppendAllText(log, DateTime.UtcNow.ToString("o") + " " + message + Environment.NewLine);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
    static int FreePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 8765);
        try { listener.Start(); }
        catch (SocketException) { listener = new TcpListener(IPAddress.Loopback, 0); listener.Start(); }
        try { return ((IPEndPoint)listener.LocalEndpoint).Port; }
        finally { listener.Stop(); }
    }
    static bool OwnsListener(int port, int pid)
    {
        int size = 0;
        Native.GetExtendedTcpTable(IntPtr.Zero, ref size, false, 2, 3, 0);
        IntPtr table = Marshal.AllocHGlobal(size);
        try
        {
            if (Native.GetExtendedTcpTable(table, ref size, false, 2, 3, 0) != 0) return false;
            for (int i = 0; i < Marshal.ReadInt32(table); i++)
            {
                int offset = 4 + i * 24;
                int localPort = (ushort)IPAddress.NetworkToHostOrder((short)Marshal.ReadInt32(table, offset + 8));
                if (Marshal.ReadInt32(table, offset + 4) == 0x0100007f && localPort == port &&
                    Marshal.ReadInt32(table, offset + 20) == pid) return true;
            }
            return false;
        }
        finally { Marshal.FreeHGlobal(table); }
    }
    static bool Healthy(string url)
    {
        try
        {
            var request = (HttpWebRequest)WebRequest.Create(url + "api/health");
            request.Proxy = null; request.AllowAutoRedirect = false; request.Timeout = 500; request.ReadWriteTimeout = 500;
            using (var response = request.GetResponse())
            using (var reader = new StreamReader(response.GetResponseStream()))
            {
                var body = Json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                return (string)body["status"] == "ok" &&
                    (string)((Dictionary<string, object>)body["runtime"])["application"] == "standalone";
            }
        }
        catch (WebException) { return false; }
        catch (IOException) { return false; }
        catch (ArgumentException) { return false; }
        catch (InvalidCastException) { return false; }
        catch (KeyNotFoundException) { return false; }
        catch (InvalidOperationException) { return false; }
        catch (NullReferenceException) { return false; }
    }
    static string Require(string path)
    { if (!File.Exists(path)) throw new FileNotFoundException("An application file is missing. Reinstall Studio.", path); return path; }
    static string Edge()
    {
        foreach (string root in new[] { Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData) })
        {
            if (String.IsNullOrEmpty(root)) continue;
            string path = Path.Combine(root, "Microsoft", "Edge", "Application", "msedge.exe");
            if (File.Exists(path)) return path;
        }
        return null;
    }
    static List<IntPtr> Windows(ProcessJob job)
    {
        var windows = new List<IntPtr>();
        var pids = job.ProcessIds();
        Native.EnumWindows(delegate(IntPtr window, IntPtr unused)
        {
            int pid; Native.GetWindowThreadProcessId(window, out pid);
            if (pids.Contains(pid) && Native.IsWindowVisible(window)) windows.Add(window);
            return true;
        }, IntPtr.Zero);
        return windows;
    }

    static void Run(ProcessJob job, EventWaitHandle stop, EventWaitHandle activate, EventWaitHandle update)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Require(Path.Combine(root, "python", "pythonw.exe"));
        string script = Require(Path.Combine(root, "app", "scripts", "studio_app.py"));
        int server = 0, port = 0;
        string url = null;
        for (int attempt = 0; attempt < 3; attempt++)
        {
            port = FreePort(); url = "http://127.0.0.1:" + port + "/";
            server = job.Start(python, new[] { "-B", script, "--no-browser", "--host", "127.0.0.1", "--port", port.ToString() }, Path.Combine(root, "app"));
            Log("Starting server pid=" + server + " port=" + port);
            var clock = Stopwatch.StartNew();
            bool ready = false;
            while (clock.Elapsed.TotalSeconds < 90 && job.Alive(server))
            {
                if (stop.WaitOne(100)) return;
                if (OwnsListener(port, server) && Healthy(url)) { ready = true; break; }
            }
            if (ready) break;
            if (job.Alive(server) || attempt == 2) throw new InvalidOperationException(
                "The local editor did not start. Check runtime\\backend.log in your data folder, then try again.");
        }
        string browser = headless ? Environment.GetEnvironmentVariable("CUTGRAPH_LAUNCHER_BROWSER") : null;
        if (headless && String.IsNullOrEmpty(browser)) throw new ArgumentException("Headless mode requires a substituted browser command.");
        bool fake = !String.IsNullOrEmpty(browser);
        if (!fake) browser = Edge();
        int browserPid = 0;
        string[] browserArguments = null;
        Form fallback = null;
        if (browser != null)
        {
            string[] arguments;
            if (fake)
            {
                if (!Path.IsPathRooted(browser)) throw new ArgumentException("The substituted browser must be an absolute executable path.");
                arguments = Json.Deserialize<string[]>(Environment.GetEnvironmentVariable("CUTGRAPH_LAUNCHER_BROWSER_ARGS") ?? "[]");
                for (int i = 0; i < arguments.Length; i++) arguments[i] = arguments[i].Replace("{url}", url + "editor/");
            }
            else
            {
                string profile = Path.Combine(state, "runtime", "edge-profile");
                Directory.CreateDirectory(profile);
                arguments = new[] { "--app=" + url + "editor/", "--user-data-dir=" + profile,
                    "--no-first-run", "--no-default-browser-check", "--disable-background-mode", "--hide-crash-restore-bubble" };
            }
            browserArguments = arguments;
            browserPid = job.Start(Require(browser), arguments, root);
        }
        else
        {
            Process.Start(new ProcessStartInfo(url + "editor/") { UseShellExecute = true });
            fallback = new Form { Text = "Cutgraph Studio", Size = new Size(420, 180),
                StartPosition = FormStartPosition.CenterScreen, Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath) };
            var label = new Label { Text = "Studio is open in your browser.\nKeep this window open while editing.\nClose it to stop Studio.",
                Dock = DockStyle.Top, Height = 75, Padding = new Padding(12) };
            var button = new Button { Text = "Stop Studio", Dock = DockStyle.Bottom, Height = 36 };
            button.Click += delegate { fallback.Close(); };
            fallback.Controls.Add(label); fallback.Controls.Add(button); fallback.Show();
        }
        File.WriteAllText(Path.Combine(state, "runtime", "launcher.json"), Json.Serialize(new {
            launcherPid = Process.GetCurrentProcess().Id, serverPid = server, browserPid = browserPid,
            port = port, url = url + "editor/", mode = fake ? "substitute" : browser == null ? "default" : "edge" }));
        Log("Ready; browser=" + (fake ? "substitute" : browser == null ? "default" : "edge"));
        var elapsed = Stopwatch.StartNew();
        bool seenWindow = false;
        int absent = 0;
        try
        {
            while (!stop.WaitOne(100))
            {
                Application.DoEvents();
                if (update.WaitOne(0))
                {
                    update.Reset();
                    pendingUpdate = Update.Accept(state);
                    if (pendingUpdate != null)
                    {
                        CloseWindows(job);
                        var shutdown = Stopwatch.StartNew();
                        while (job.Alive(server) && shutdown.Elapsed.TotalSeconds < 30) Thread.Sleep(100);
                        if (job.Alive(server))
                        {
                            pendingUpdate.Dispose(); pendingUpdate = null;
                            throw new IOException("Studio did not finish shutting down; the update was not started.");
                        }
                        return;
                    }
                }
                if (!job.Alive(server)) throw new InvalidOperationException("The local editor stopped unexpectedly. Restart Studio to continue.");
                if (fallback != null && fallback.IsDisposed) return;
                var windows = fake || fallback != null ? new List<IntPtr>() : Windows(job);
                if (windows.Count > 0) { seenWindow = true; absent = 0; }
                else if (seenWindow && ++absent >= 15) return;
                if (fake && !job.Alive(browserPid)) return;
                if (!fake && fallback == null && !seenWindow && elapsed.Elapsed.TotalSeconds > 45)
                    throw new InvalidOperationException("The editor window could not open. Close Studio and try again.");
                if (activate.WaitOne(0))
                {
                    Log("Activate requested.");
                    if (fallback != null) { fallback.Show(); fallback.Activate(); Process.Start(url + "editor/"); }
                    else if (!fake && seenWindow && windows.Count == 0)
                    {
                        browserPid = job.Start(browser, browserArguments, root);
                        seenWindow = false; absent = 0; elapsed.Restart();
                    }
                    else foreach (IntPtr window in windows) { Native.ShowWindow(window, 9); Native.SetForegroundWindow(window); }
                    activate.Reset();
                }
            }
        }
        finally
        {
            if (fallback != null) fallback.Dispose();
        }
    }
    static void CloseWindows(ProcessJob job)
    {
        var windows = Windows(job);
        foreach (IntPtr window in windows) Native.PostMessage(window, 0x0010, IntPtr.Zero, IntPtr.Zero);
        var grace = Stopwatch.StartNew();
        while (job.Active > 1 && grace.Elapsed.TotalSeconds < 3)
        { Application.DoEvents(); Thread.Sleep(50); }
    }
}
