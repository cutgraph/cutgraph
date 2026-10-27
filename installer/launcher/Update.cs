using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Threading;
using Microsoft.Win32.SafeHandles;

internal sealed class Update : IDisposable
{
    internal readonly string Path;
    internal readonly string Id;
    readonly FileStream file;
    readonly SafeFileHandle directory;
    Update(string path, string id, FileStream stream, SafeFileHandle folder)
    { Path = path; Id = id; file = stream; directory = folder; }

    internal static Update Verify(string state)
    {
        string folder = System.IO.Path.Combine(state, "updates");
        string canonical = DataPaths.Canonical(folder);
        if (!String.Equals(canonical, System.IO.Path.GetFullPath(folder).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
            throw new IOException("The updates folder must not contain links.");
        // Hold the parent as well as the executable against renaming or replacement.
        SafeFileHandle directory = DataPaths.HoldDirectory(folder);
        FileStream file = null;
        try
        {
            string request = System.IO.Path.Combine(folder, "pending.json");
            if (new FileInfo(request).Length > 4096) throw new IOException("Invalid update request size.");
            var data = Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(request));
            string path = (string)data["path"], hash = (string)data["sha256"], id = (string)data["id"];
            if (!System.Text.RegularExpressions.Regex.IsMatch(hash, "\\A[0-9a-f]{64}\\z") ||
                !System.Text.RegularExpressions.Regex.IsMatch(id, "\\A[0-9a-f]{32}\\z")) throw new IOException("Invalid update identity.");
            string expected = System.IO.Path.Combine(folder, "installer-" + hash + ".exe");
            if (!String.Equals(path, expected, StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(DataPaths.Canonical(path), expected, StringComparison.OrdinalIgnoreCase))
                throw new IOException("The installer must be inside the updates folder without links.");
            Version target;
            Version current = new Version(FileVersionInfo.GetVersionInfo(ApplicationPath()).FileVersion);
            if (!Version.TryParse((string)data["version"], out target) || target.Build < 0 ||
                new Version(target.Major, target.Minor, target.Build, 0) <= current)
                throw new IOException("The launcher refuses downgrades and same-version updates.");
            file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (file.Length != Convert.ToInt64(data["size"])) throw new IOException("Installer size verification failed.");
            using (var sha = SHA256.Create())
                if (BitConverter.ToString(sha.ComputeHash(file)).Replace("-", "").ToLowerInvariant() != hash)
                    throw new IOException("Installer SHA-256 verification failed.");
            // Authenticode publisher verification is disabled until releases are signed.
            return new Update(path, id, file, directory);
        }
        catch { if (file != null) file.Dispose(); directory.Dispose(); throw; }
    }
    static string ApplicationPath() { return Process.GetCurrentProcess().MainModule.FileName; }
    internal static int Request(string state, SessionObjects objects)
    {
        using (var update = Verify(state))
        using (var owner = objects.TryOpenOwner())
        {
            if (owner == null) throw new IOException("Start Studio with its installed launcher before updating.");
            using (var signal = objects.TryOpenEvent("update", owner))
            {
                if (signal == null) throw new IOException("The running launcher cannot accept updates.");
                string receipt = System.IO.Path.Combine(state, "updates", "response.json");
                File.Delete(receipt);
                signal.Set();
                var clock = Stopwatch.StartNew();
                while (clock.Elapsed.TotalSeconds < 20 && !owner.HasExited)
                {
                    if (File.Exists(receipt))
                    {
                        var data = Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(receipt));
                        if ((string)data["id"] == update.Id) return (bool)data["accepted"] ? 0 : 1;
                    }
                    Thread.Sleep(50);
                }
                throw new IOException("The launcher did not acknowledge the update.");
            }
        }
    }
    internal static Update Accept(string state)
    {
        Update update = null;
        try
        {
            update = Verify(state);
            string receipt = System.IO.Path.Combine(state, "updates", "response.json");
            File.WriteAllText(receipt + ".tmp", Program.Json.Serialize(new { id = update.Id, accepted = true }));
            if (File.Exists(receipt)) File.Delete(receipt);
            File.Move(receipt + ".tmp", receipt);
            return update;
        }
        catch (Exception error)
        {
            if (update != null) update.Dispose();
            Program.Log("Update refused: " + error.Message);
            return null;
        }
    }
    internal void Launch()
    {
        Process.Start(new ProcessStartInfo(Path, "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /CUTGRAPHUPDATE=1")
        { UseShellExecute = false, WorkingDirectory = System.IO.Path.GetDirectoryName(Path) });
    }
    public void Dispose() { file.Dispose(); directory.Dispose(); }
}
