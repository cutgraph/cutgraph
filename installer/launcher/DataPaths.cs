using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
using System.Collections.Generic;

internal static class DataPaths
{
    const uint IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003;
    const uint IO_REPARSE_TAG_SYMLINK = 0xA000000C;
    internal sealed class LinkedPathException : IOException
    { internal LinkedPathException() : base("The selected folder contains a symbolic link, junction or mount point. Remove the link before continuing.") { } }
    internal static bool IsLink(uint tag)
    { return tag == IO_REPARSE_TAG_SYMLINK || tag == IO_REPARSE_TAG_MOUNT_POINT; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security,
        uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path, uint size, uint flags);
    [StructLayout(LayoutKind.Sequential)] struct AttributeTag
    { internal uint attributes, tag; }
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetFileInformationByHandleEx(SafeFileHandle handle, int kind, out AttributeTag info, uint size);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetFileInformationByHandle(SafeFileHandle handle, int kind, byte[] info, uint size);

    internal static SafeFileHandle HoldDirectory(string path)
    {
        var handle = CreateFile(path, 0x80, 1, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
        Native.Check(!handle.IsInvalid);
        return handle;
    }

    internal static bool Absolute(string value)
    { return Path.IsPathRooted(value) && (value.StartsWith(@"\\") || (value.Length > 2 && value[1] == ':' && value[2] == '\\')); }
    internal static string Canonical(string value)
    {
        string path = Path.GetFullPath(value).TrimEnd('\\');
        if (path.Length == 2 && path[1] == ':') path += "\\";
        if (!File.Exists(path) && !Directory.Exists(path))
        {
            string parent = Path.GetDirectoryName(path);
            if (String.IsNullOrEmpty(parent)) throw new IOException("The selected path is unavailable.");
            return Path.Combine(Canonical(parent), Path.GetFileName(path));
        }
        using (var handle = CreateFile(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero))
        {
            Native.Check(!handle.IsInvalid);
            var result = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandle(handle, result, (uint)result.Capacity, 0);
            Native.Check(length > 0 && length < result.Capacity);
            string final = result.ToString();
            if (final.StartsWith(@"\\?\UNC\")) final = @"\\" + final.Substring(8);
            else if (final.StartsWith(@"\\?\")) final = final.Substring(4);
            return final.TrimEnd('\\');
        }
    }
    internal static bool Overlaps(string first, string second)
    {
        string a = Canonical(first) + "\\", b = Canonical(second) + "\\";
        return a.StartsWith(b, StringComparison.OrdinalIgnoreCase) || b.StartsWith(a, StringComparison.OrdinalIgnoreCase);
    }
    sealed class DeleteNode : IDisposable
    {
        readonly SafeFileHandle handle;
        readonly List<DeleteNode> children = new List<DeleteNode>();
        internal DeleteNode(string target, bool program = false, bool deleting = true)
        {
            // Open the entry itself, never its reparse target. Denying write/delete sharing
            // holds every validated directory stable through enumeration and deletion.
            handle = CreateFile(target, deleting ? 0x10080u : 0x80u, 1, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            try
            {
                Native.Check(!handle.IsInvalid);
                AttributeTag info;
                Native.Check(GetFileInformationByHandleEx(handle, 9, out info, 8));
                if ((info.attributes & 0x400) != 0 && IsLink(info.tag)) throw new LinkedPathException();
                if ((info.attributes & 0x10) != 0)
                    foreach (string child in Directory.GetFileSystemEntries(target))
                        if (!program || !InstallMetadata(Path.GetFileName(child))) children.Add(new DeleteNode(child, false, deleting));
            }
            catch { Dispose(); throw; }
        }
        internal void Delete()
        {
            DeleteChildren();
            // FileDispositionInfoEx: delete with POSIX semantics, including read-only files.
            Native.Check(SetFileInformationByHandle(handle, 21, BitConverter.GetBytes(0x13u), 4));
        }
        internal void DeleteChildren()
        { foreach (var child in children) { child.Delete(); child.Dispose(); } }
        public void Dispose()
        {
            foreach (var child in children) child.Dispose();
            handle.Dispose();
        }
    }
    static bool InstallMetadata(string filename)
    {
        foreach (string name in new[] { ".cutgraph-install", "unins000.exe", "unins000.dat", "unins000.msg" })
            if (String.Equals(name, filename, StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }
    internal static void CheckTree(string application)
    {
        if (!Directory.Exists(application) && !File.Exists(application)) return;
        using (var tree = new DeleteNode(application, false, false)) { }
    }
    internal static void ClearProgramFiles(string application)
    {
        string full = Path.GetFullPath(application).TrimEnd('\\');
        string parent = Path.GetDirectoryName(full);
        if (String.IsNullOrEmpty(parent)) throw new IOException("The application folder is invalid.");
        string target = Path.Combine(Canonical(parent), Path.GetFileName(full));
        bool section = false, owned = false;
        foreach (string line in File.ReadAllLines(Path.Combine(target, ".cutgraph-install")))
        {
            string text = line.Trim();
            if (text.StartsWith("[")) section = String.Equals(text, "[Install]", StringComparison.OrdinalIgnoreCase);
            else if (section && String.Equals(text, "Id=A0352AC4-4E53-4582-84D1-B1D719AA4FD4", StringComparison.OrdinalIgnoreCase)) owned = true;
        }
        if (!owned) throw new IOException("The application folder is not a recognized installation.");
        using (var tree = new DeleteNode(target, true)) tree.DeleteChildren();
    }
    internal static void RemoveAddons(string state, string application)
    {
        if (Overlaps(state, application)) throw new IOException("Application and data folders must be separate.");
        string target = Path.Combine(Canonical(state), "addons");
        if (!Directory.Exists(target) && !File.Exists(target)) return;
        // A redirected parent is allowed. Validate and lock the entire deletion tree
        // before removing anything, then delete by those handles instead of reopening paths.
        using (var tree = new DeleteNode(target)) tree.Delete();
    }
}
