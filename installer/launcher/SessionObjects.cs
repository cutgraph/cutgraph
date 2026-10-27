using System;
using System.Diagnostics;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;

internal sealed class SessionObjects : IDisposable
{
    readonly SecurityIdentifier user = WindowsIdentity.GetCurrent().User;
    readonly string prefix;
    IntPtr boundary, space;
    internal readonly Mutex Mutex;
    MemoryMappedFile owner;

    [StructLayout(LayoutKind.Sequential)] struct SecurityAttributes
    { internal int length; internal IntPtr descriptor; internal int inherit; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateBoundaryDescriptor(string name, uint flags);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AddSIDToBoundaryDescriptor(ref IntPtr boundary, byte[] sid);
    [DllImport("kernel32.dll")] static extern void DeleteBoundaryDescriptor(IntPtr boundary);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreatePrivateNamespace(ref SecurityAttributes security, IntPtr boundary, string alias);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr OpenPrivateNamespace(IntPtr boundary, string alias);
    [DllImport("kernel32.dll")] static extern bool ClosePrivateNamespace(IntPtr space, uint flags);

    internal SessionObjects(string installation)
    {
        // Other accounts may share a desktop session and know the install path. A SID boundary
        // isolates even pre-created names; protected user-only DACLs and owner checks prevent
        // foreign stop/activation objects. Same-user processes and administrators are trusted.
        string identity = user.Value + "|" + Process.GetCurrentProcess().SessionId + "|" + DataPaths.Canonical(installation);
        using (var hash = SHA256.Create()) prefix = "CutgraphStudio." + user.Value + "." +
            BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(identity.ToUpperInvariant()))).Replace("-", "");
        try
        {
            boundary = CreateBoundaryDescriptor(prefix, 0);
            Native.Check(boundary != IntPtr.Zero);
            byte[] sid = new byte[user.BinaryLength]; user.GetBinaryForm(sid, 0);
            Native.Check(AddSIDToBoundaryDescriptor(ref boundary, sid));
            var descriptor = new RawSecurityDescriptor("O:" + user.Value + "D:P(A;;GA;;;" + user.Value + ")");
            byte[] bytes = new byte[descriptor.BinaryLength]; descriptor.GetBinaryForm(bytes, 0);
            IntPtr memory = Marshal.AllocHGlobal(bytes.Length);
            try
            {
                Marshal.Copy(bytes, 0, memory, bytes.Length);
                var attributes = new SecurityAttributes { length = Marshal.SizeOf(typeof(SecurityAttributes)), descriptor = memory };
                space = CreatePrivateNamespace(ref attributes, boundary, prefix);
                if (space == IntPtr.Zero && Marshal.GetLastWin32Error() == 183)
                    space = OpenPrivateNamespace(boundary, prefix);
                Native.Check(space != IntPtr.Zero);
            }
            finally { Marshal.FreeHGlobal(memory); }
            var security = new MutexSecurity();
            security.SetOwner(user); security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new MutexAccessRule(user, MutexRights.FullControl, AccessControlType.Allow));
            bool created;
            Mutex = new Mutex(false, prefix + "\\instance", out created, security);
            CheckOwner(Mutex.GetAccessControl());
        }
        catch { Dispose(); throw; }
    }
    void CheckOwner(ObjectSecurity security)
    {
        if (!user.Equals(security.GetOwner(typeof(SecurityIdentifier))))
            throw new UnauthorizedAccessException("Studio coordination object has an unexpected owner.");
        foreach (AuthorizationRule rule in ((CommonObjectSecurity)security).GetAccessRules(true, true, typeof(SecurityIdentifier)))
            if (!user.Equals(rule.IdentityReference))
                throw new UnauthorizedAccessException("Studio coordination object has an unexpected access rule.");
    }
    internal static long Created(Process process)
    {
        long created, exited, kernel, userTime;
        Native.Check(Native.GetProcessTimes(process.Handle, out created, out exited, out kernel, out userTime));
        return created;
    }
    string EventName(string suffix, Process process)
    { return prefix + "\\" + suffix + "." + process.Id + "." + Created(process); }
    internal EventWaitHandle CreateEvent(string suffix)
    {
        var security = new EventWaitHandleSecurity();
        security.SetOwner(user); security.SetAccessRuleProtection(true, false);
        security.AddAccessRule(new EventWaitHandleAccessRule(user, EventWaitHandleRights.FullControl, AccessControlType.Allow));
        bool created;
        EventWaitHandle result;
        using (var process = Process.GetCurrentProcess())
            result = new EventWaitHandle(false, suffix == "activate" ? EventResetMode.ManualReset : EventResetMode.AutoReset,
                EventName(suffix, process), out created, security);
        if (!created) { result.Dispose(); throw new IOException("Studio coordination event already exists."); }
        CheckOwner(result.GetAccessControl());
        return result;
    }
    internal void PublishOwner()
    {
        var security = new MemoryMappedFileSecurity();
        security.SetOwner(user); security.SetAccessRuleProtection(true, false);
        security.AddAccessRule(new AccessRule<MemoryMappedFileRights>(user, MemoryMappedFileRights.FullControl, AccessControlType.Allow));
        owner = MemoryMappedFile.CreateNew(prefix + "\\owner", 16, MemoryMappedFileAccess.ReadWrite,
            MemoryMappedFileOptions.None, security, HandleInheritability.None);
        using (var view = owner.CreateViewAccessor())
        using (var process = Process.GetCurrentProcess())
        { view.Write(8, Created(process)); view.Write(0, process.Id); }
    }
    internal void UnpublishOwner()
    { if (owner != null) { owner.Dispose(); owner = null; } }
    internal Process TryOpenOwner()
    {
        try
        {
            using (var mapping = MemoryMappedFile.OpenExisting(prefix + "\\owner", MemoryMappedFileRights.ReadPermissions | MemoryMappedFileRights.Read))
            {
                CheckOwner(mapping.GetAccessControl());
                using (var view = mapping.CreateViewAccessor(0, 16, MemoryMappedFileAccess.Read))
                {
                    int pid = view.ReadInt32(0);
                    if (pid == 0) return null;
                    Process process;
                    try { process = Process.GetProcessById(pid); }
                    catch (ArgumentException) { return null; }
                    try
                    {
                        if (!process.HasExited && Created(process) == view.ReadInt64(8)) return process;
                        process.Dispose(); return null;
                    }
                    catch (InvalidOperationException) { process.Dispose(); return null; }
                    catch { process.Dispose(); throw; }
                }
            }
        }
        catch (FileNotFoundException) { return null; }
    }
    internal EventWaitHandle TryOpenEvent(string suffix, Process process)
    {
        try
        {
            var signal = EventWaitHandle.OpenExisting(EventName(suffix, process),
                EventWaitHandleRights.Modify | EventWaitHandleRights.ReadPermissions | EventWaitHandleRights.Synchronize);
            try { CheckOwner(signal.GetAccessControl()); return signal; }
            catch { signal.Dispose(); throw; }
        }
        catch (WaitHandleCannotBeOpenedException) { return null; }
        catch (InvalidOperationException) { return null; }
    }
    public void Dispose()
    {
        UnpublishOwner();
        if (Mutex != null) Mutex.Dispose();
        if (space != IntPtr.Zero) { ClosePrivateNamespace(space, 0); space = IntPtr.Zero; }
        if (boundary != IntPtr.Zero) { DeleteBoundaryDescriptor(boundary); boundary = IntPtr.Zero; }
    }
}
