$ErrorActionPreference = 'Stop'

function Test-GrandeAlphaFullyQualifiedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        return $false
    }
    try {
        $Expanded = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    } catch {
        return $false
    }
    return [string]::Equals(
        $Expanded,
        $Path.TrimEnd('\'),
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Write-GrandeAlphaAtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $Directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $TemporaryPath = "$Path.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        $Json = $Value | ConvertTo-Json -Depth 6
        $Utf8NoBom = New-Object Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($TemporaryPath, $Json, $Utf8NoBom)
        Move-Item -LiteralPath $TemporaryPath -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $TemporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $TemporaryPath -Force
        }
    }
}

function Read-GrandeAlphaJsonFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
}

function Get-GrandeAlphaPythonProcessExecutable([string]$PythonLauncher) {
    $Launcher = [IO.Path]::GetFullPath($PythonLauncher)
    $RuntimeRoot = Split-Path -Parent (Split-Path -Parent $Launcher)
    $VirtualEnvironmentConfig = Join-Path $RuntimeRoot 'pyvenv.cfg'
    if (Test-Path -LiteralPath $VirtualEnvironmentConfig -PathType Leaf) {
        foreach ($Line in Get-Content -LiteralPath $VirtualEnvironmentConfig -Encoding utf8) {
            if ($Line -match '^\s*executable\s*=\s*(.+?)\s*$') {
                $BaseExecutable = [IO.Path]::GetFullPath($Matches[1])
                if (Test-Path -LiteralPath $BaseExecutable -PathType Leaf) {
                    return $BaseExecutable
                }
            }
        }
    }
    return $Launcher
}

function Initialize-GrandeAlphaSuspendedJobProcessType {
    if ($null -ne ('GrandeAlpha.Lifecycle.SuspendedJobProcess' -as [type])) {
        return
    }

    # PROC_THREAD_ATTRIBUTE_JOB_LIST makes membership atomic with CreateProcessW.
    # CREATE_SUSPENDED then provides a persistence barrier: the exact PID and start
    # time can be committed before any Python application code is allowed to run.
    $Source = @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace GrandeAlpha.Lifecycle
{
    public sealed class SuspendedJobProcess : IDisposable
    {
        private const uint CREATE_SUSPENDED = 0x00000004;
        private const uint EXTENDED_STARTUPINFO_PRESENT = 0x00080000;
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
        private const int JobObjectExtendedLimitInformation = 9;
        private static readonly IntPtr PROC_THREAD_ATTRIBUTE_JOB_LIST = new IntPtr(0x0002000D);

        private readonly object sync = new object();
        private IntPtr jobHandle;
        private IntPtr threadHandle;
        private Process process;
        private bool resumed;
        private bool disposed;

        private SuspendedJobProcess(IntPtr jobHandle, IntPtr threadHandle, Process process)
        {
            this.jobHandle = jobHandle;
            this.threadHandle = threadHandle;
            this.process = process;
        }

        public Process Process
        {
            get
            {
                lock (sync)
                {
                    ThrowIfDisposed();
                    return process;
                }
            }
        }

        public static SuspendedJobProcess Start(
            string executable,
            string arguments,
            string workingDirectory)
        {
            if (String.IsNullOrWhiteSpace(executable))
                throw new ArgumentException("An exact executable path is required.", "executable");
            if (arguments == null)
                throw new ArgumentNullException("arguments");
            if (String.IsNullOrWhiteSpace(workingDirectory))
                throw new ArgumentException("An exact working directory is required.", "workingDirectory");

            IntPtr job = IntPtr.Zero;
            IntPtr attributeList = IntPtr.Zero;
            IntPtr jobListValue = IntPtr.Zero;
            PROCESS_INFORMATION processInformation = new PROCESS_INFORMATION();
            Process managedProcess = null;
            bool attributeListInitialized = false;
            bool ownershipTransferred = false;

            try
            {
                job = CreateJobObjectW(IntPtr.Zero, null);
                if (job == IntPtr.Zero)
                    throw NewWin32Exception("CreateJobObjectW");

                JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits =
                    new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if (!SetInformationJobObject(
                    job,
                    JobObjectExtendedLimitInformation,
                    ref limits,
                    (uint)Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION))))
                {
                    throw NewWin32Exception("SetInformationJobObject");
                }

                IntPtr attributeListSize = IntPtr.Zero;
                InitializeProcThreadAttributeList(
                    IntPtr.Zero,
                    1,
                    0,
                    ref attributeListSize);
                if (attributeListSize == IntPtr.Zero)
                    throw NewWin32Exception("InitializeProcThreadAttributeList(size)");

                attributeList = Marshal.AllocHGlobal(attributeListSize);
                if (!InitializeProcThreadAttributeList(
                    attributeList,
                    1,
                    0,
                    ref attributeListSize))
                {
                    throw NewWin32Exception("InitializeProcThreadAttributeList");
                }
                attributeListInitialized = true;

                jobListValue = Marshal.AllocHGlobal(IntPtr.Size);
                Marshal.WriteIntPtr(jobListValue, job);
                if (!UpdateProcThreadAttribute(
                    attributeList,
                    0,
                    PROC_THREAD_ATTRIBUTE_JOB_LIST,
                    jobListValue,
                    new IntPtr(IntPtr.Size),
                    IntPtr.Zero,
                    IntPtr.Zero))
                {
                    throw NewWin32Exception("UpdateProcThreadAttribute(JOB_LIST)");
                }

                STARTUPINFOEX startupInfo = new STARTUPINFOEX();
                startupInfo.StartupInfo.cb = Marshal.SizeOf(typeof(STARTUPINFOEX));
                startupInfo.lpAttributeList = attributeList;
                StringBuilder commandLine = new StringBuilder(
                    QuoteCommandLineArgument(executable) +
                    (arguments.Length == 0 ? String.Empty : " " + arguments));

                if (!CreateProcessW(
                    executable,
                    commandLine,
                    IntPtr.Zero,
                    IntPtr.Zero,
                    false,
                    CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT,
                    IntPtr.Zero,
                    workingDirectory,
                    ref startupInfo,
                    out processInformation))
                {
                    throw NewWin32Exception("CreateProcessW");
                }

                bool isInJob;
                if (!IsProcessInJob(processInformation.hProcess, job, out isInJob))
                    throw NewWin32Exception("IsProcessInJob");
                if (!isInJob)
                    throw new InvalidOperationException(
                        "Windows created the child without the required lifecycle job membership.");

                managedProcess = Process.GetProcessById((int)processInformation.dwProcessId);
                // Force Windows to resolve the immutable creation time while the
                // primary thread is still suspended and the raw handle is valid.
                DateTime ignored = managedProcess.StartTime.ToUniversalTime();

                SuspendedJobProcess result = new SuspendedJobProcess(
                    job,
                    processInformation.hThread,
                    managedProcess);
                job = IntPtr.Zero;
                processInformation.hThread = IntPtr.Zero;
                managedProcess = null;
                ownershipTransferred = true;
                return result;
            }
            catch
            {
                if (processInformation.hProcess != IntPtr.Zero)
                    TerminateProcess(processInformation.hProcess, 1);
                throw;
            }
            finally
            {
                if (attributeListInitialized)
                    DeleteProcThreadAttributeList(attributeList);
                if (attributeList != IntPtr.Zero)
                    Marshal.FreeHGlobal(attributeList);
                if (jobListValue != IntPtr.Zero)
                    Marshal.FreeHGlobal(jobListValue);
                if (processInformation.hProcess != IntPtr.Zero)
                    CloseHandle(processInformation.hProcess);
                if (processInformation.hThread != IntPtr.Zero)
                    CloseHandle(processInformation.hThread);
                if (job != IntPtr.Zero)
                    CloseHandle(job);
                if (!ownershipTransferred && managedProcess != null)
                    managedProcess.Dispose();
            }
        }

        public void Resume()
        {
            lock (sync)
            {
                ThrowIfDisposed();
                if (resumed)
                    throw new InvalidOperationException("The child primary thread was already resumed.");
                uint previousSuspendCount = ResumeThread(threadHandle);
                if (previousSuspendCount == UInt32.MaxValue)
                    throw NewWin32Exception("ResumeThread");
                resumed = true;
                CloseHandle(threadHandle);
                threadHandle = IntPtr.Zero;
            }
        }

        public void Dispose()
        {
            Dispose(true);
            GC.SuppressFinalize(this);
        }

        ~SuspendedJobProcess()
        {
            Dispose(false);
        }

        private void Dispose(bool disposing)
        {
            lock (sync)
            {
                if (disposed)
                    return;
                disposed = true;
                if (threadHandle != IntPtr.Zero)
                {
                    CloseHandle(threadHandle);
                    threadHandle = IntPtr.Zero;
                }
                // Closing the last anonymous job handle is the containment action.
                // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE covers the launcher and every
                // non-breakaway descendant it creates.
                if (jobHandle != IntPtr.Zero)
                {
                    CloseHandle(jobHandle);
                    jobHandle = IntPtr.Zero;
                }
                if (disposing && process != null)
                    process.Dispose();
                process = null;
            }
        }

        private void ThrowIfDisposed()
        {
            if (disposed)
                throw new ObjectDisposedException("SuspendedJobProcess");
        }

        private static string QuoteCommandLineArgument(string value)
        {
            if (value.IndexOf('\"') >= 0)
                throw new ArgumentException("Executable paths cannot contain a quote.", "value");
            return "\"" + value + "\"";
        }

        private static Win32Exception NewWin32Exception(string operation)
        {
            int error = Marshal.GetLastWin32Error();
            return new Win32Exception(error, operation + " failed: " + new Win32Exception(error).Message);
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct STARTUPINFO
        {
            public int cb;
            public IntPtr lpReserved;
            public IntPtr lpDesktop;
            public IntPtr lpTitle;
            public int dwX;
            public int dwY;
            public int dwXSize;
            public int dwYSize;
            public int dwXCountChars;
            public int dwYCountChars;
            public int dwFillAttribute;
            public int dwFlags;
            public short wShowWindow;
            public short cbReserved2;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct STARTUPINFOEX
        {
            public STARTUPINFO StartupInfo;
            public IntPtr lpAttributeList;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_INFORMATION
        {
            public IntPtr hProcess;
            public IntPtr hThread;
            public uint dwProcessId;
            public uint dwThreadId;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObjectW(IntPtr jobAttributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(
            IntPtr job,
            int informationClass,
            ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION information,
            uint informationLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool InitializeProcThreadAttributeList(
            IntPtr attributeList,
            int attributeCount,
            int flags,
            ref IntPtr size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UpdateProcThreadAttribute(
            IntPtr attributeList,
            uint flags,
            IntPtr attribute,
            IntPtr value,
            IntPtr size,
            IntPtr previousValue,
            IntPtr returnSize);

        [DllImport("kernel32.dll")]
        private static extern void DeleteProcThreadAttributeList(IntPtr attributeList);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcessW(
            string applicationName,
            StringBuilder commandLine,
            IntPtr processAttributes,
            IntPtr threadAttributes,
            bool inheritHandles,
            uint creationFlags,
            IntPtr environment,
            string currentDirectory,
            ref STARTUPINFOEX startupInfo,
            out PROCESS_INFORMATION processInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool IsProcessInJob(
            IntPtr process,
            IntPtr job,
            [MarshalAs(UnmanagedType.Bool)] out bool result);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr thread);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(IntPtr process, uint exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);
    }
}
'@
    Add-Type -TypeDefinition $Source -Language CSharp | Out-Null
}

function Start-GrandeAlphaSuspendedJobProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    if (
        -not (Test-GrandeAlphaFullyQualifiedPath $Executable) -or
        -not (Test-Path -LiteralPath $Executable -PathType Leaf) -or
        -not (Test-GrandeAlphaFullyQualifiedPath $WorkingDirectory) -or
        -not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)
    ) {
        throw 'Suspended job launch requires existing, fully qualified executable and working-directory paths.'
    }
    Initialize-GrandeAlphaSuspendedJobProcessType
    return [GrandeAlpha.Lifecycle.SuspendedJobProcess]::Start(
        [IO.Path]::GetFullPath($Executable),
        $Arguments,
        [IO.Path]::GetFullPath($WorkingDirectory)
    )
}

function Get-GrandeAlphaProcessSnapshot([int]$ProcessId) {
    if ($ProcessId -le 0) {
        return $null
    }
    $NativeProcess = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $NativeProcess) {
        return $null
    }
    try {
        $CimProcess = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if ($null -eq $CimProcess) {
            return $null
        }
        $Owner = Invoke-CimMethod -InputObject $CimProcess -MethodName GetOwnerSid -ErrorAction Stop
        if ([uint32]$Owner.ReturnValue -ne 0 -or [string]::IsNullOrWhiteSpace([string]$Owner.Sid)) {
            return $null
        }
        return [pscustomobject]@{
            process_id = [int]$CimProcess.ProcessId
            parent_process_id = [int]$CimProcess.ParentProcessId
            executable_path = [string]$CimProcess.ExecutablePath
            command_line = [string]$CimProcess.CommandLine
            owner_sid = [string]$Owner.Sid
            started_at_utc = $NativeProcess.StartTime.ToUniversalTime().ToString('o')
            native_process = $NativeProcess
        }
    } catch {
        return $null
    }
}

function Get-GrandeAlphaDirectChildProcessSnapshots([int]$ParentProcessId) {
    if ($ParentProcessId -le 0) {
        return @()
    }
    try {
        $Children = @(
            Get-CimInstance `
                -ClassName Win32_Process `
                -Filter "ParentProcessId = $ParentProcessId" `
                -ErrorAction Stop
        )
    } catch {
        return @()
    }
    $Snapshots = [Collections.Generic.List[object]]::new()
    foreach ($Child in $Children) {
        $Snapshot = Get-GrandeAlphaProcessSnapshot ([int]$Child.ProcessId)
        if (
            $null -ne $Snapshot -and
            [int]$Snapshot.parent_process_id -eq $ParentProcessId
        ) {
            $Snapshots.Add($Snapshot)
        }
    }
    return $Snapshots.ToArray()
}

function Test-GrandeAlphaProcessIdentity {
    param(
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)][int]$ExpectedProcessId,
        [Parameter(Mandatory = $true)][string]$ExpectedStartedAtUtc,
        [Parameter(Mandatory = $true)][string]$ExpectedExecutable,
        [Parameter(Mandatory = $true)][string]$ExpectedArguments,
        [Parameter(Mandatory = $true)][string]$ExpectedOwnerSid
    )

    try {
        $ExpectedStartedAt = [datetimeoffset]::Parse(
            $ExpectedStartedAtUtc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $ObservedStartedAt = [datetimeoffset]::Parse(
            [string]$Snapshot.started_at_utc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
    } catch {
        return $false
    }
    if ([int]$Snapshot.process_id -ne $ExpectedProcessId) {
        return $false
    }
    if (-not [string]::Equals(
        [string]$Snapshot.owner_sid,
        $ExpectedOwnerSid,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        return $false
    }
    try {
        if (-not [string]::Equals(
            [IO.Path]::GetFullPath([string]$Snapshot.executable_path),
            [IO.Path]::GetFullPath($ExpectedExecutable),
            [StringComparison]::OrdinalIgnoreCase
        )) {
            return $false
        }
        if ($ObservedStartedAt.UtcDateTime.Ticks -ne $ExpectedStartedAt.UtcDateTime.Ticks) {
            return $false
        }

        $ExecutablePattern = [regex]::Escape([IO.Path]::GetFullPath($ExpectedExecutable))
        $ExecutablePrefix = "^\s*(?:`"$ExecutablePattern`"|$ExecutablePattern)\s+"
        $PrefixMatch = [regex]::Match(
            [string]$Snapshot.command_line,
            $ExecutablePrefix,
            [Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
        if (-not $PrefixMatch.Success) {
            return $false
        }
        $ObservedArguments = ([string]$Snapshot.command_line).Substring($PrefixMatch.Length).TrimEnd()
        return [string]::Equals(
            $ObservedArguments,
            $ExpectedArguments,
            [StringComparison]::Ordinal
        )
    } catch {
        return $false
    }
}

function Test-GrandeAlphaLifecycleRecord {
    param(
        [Parameter(Mandatory = $true)]$Record,
        [Parameter(Mandatory = $true)][string]$ExpectedProjectRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedLauncher,
        [Parameter(Mandatory = $true)][string]$ExpectedPythonLauncher,
        [Parameter(Mandatory = $true)][string]$ExpectedPythonProcessExecutable,
        [Parameter(Mandatory = $true)][string]$ExpectedOwnerSid,
        [Parameter(Mandatory = $true)][string]$ExpectedTaskName
    )

    try {
        $ParsedInstanceId = [guid]::Empty
        $InstanceValid = [guid]::TryParse([string]$Record.instance_id, [ref]$ParsedInstanceId)
        $WrapperStartedAt = [datetimeoffset]::Parse(
            [string]$Record.wrapper_started_at_utc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        )
        [void][datetimeoffset]::Parse(
            [string]$Record.observed_at_utc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        )
        $ChildFieldsValid = (
            ($null -eq $Record.child_process_id -and $null -eq $Record.child_started_at_utc) -or
            (
                [int]$Record.child_process_id -gt 0 -and
                -not [string]::IsNullOrWhiteSpace([string]$Record.child_started_at_utc)
            )
        )
        if ($null -ne $Record.child_process_id) {
            $ChildStartedAt = [datetimeoffset]::Parse(
                [string]$Record.child_started_at_utc,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            )
            $ChildStartOrderValid = (
                $ChildStartedAt.ToUniversalTime().UtcDateTime.Ticks -ge
                $WrapperStartedAt.ToUniversalTime().UtcDateTime.Ticks
            )
        } else {
            $ChildStartOrderValid = $true
        }
        return (
            [int]$Record.schema_version -eq 1 -and
            $InstanceValid -and
            [string]$Record.state -in @(
                'starting',
                'launching',
                'running',
                'restart_wait',
                'clean_exit',
                'failed'
            ) -and
            [string]$Record.task_name -eq $ExpectedTaskName -and
            [string]$Record.mode -eq '--auto-shadow' -and
            $Record.read_only -eq $true -and
            $Record.broker_writes -eq $false -and
            $Record.live_authority -eq $false -and
            [string]$Record.owner_sid -eq $ExpectedOwnerSid -and
            [int]$Record.wrapper_process_id -gt 0 -and
            $ChildFieldsValid -and
            $ChildStartOrderValid -and
            (Test-GrandeAlphaFullyQualifiedPath ([string]$Record.project_root)) -and
            (Test-GrandeAlphaFullyQualifiedPath ([string]$Record.launcher_path)) -and
            (Test-GrandeAlphaFullyQualifiedPath ([string]$Record.python_executable)) -and
            (Test-GrandeAlphaFullyQualifiedPath ([string]$Record.python_process_executable)) -and
            [string]::Equals(
                [IO.Path]::GetFullPath([string]$Record.project_root),
                [IO.Path]::GetFullPath($ExpectedProjectRoot),
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            [string]::Equals(
                [IO.Path]::GetFullPath([string]$Record.launcher_path),
                [IO.Path]::GetFullPath($ExpectedLauncher),
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            [string]::Equals(
                [IO.Path]::GetFullPath([string]$Record.python_executable),
                [IO.Path]::GetFullPath($ExpectedPythonLauncher),
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            [string]::Equals(
                [IO.Path]::GetFullPath([string]$Record.python_process_executable),
                [IO.Path]::GetFullPath($ExpectedPythonProcessExecutable),
                [StringComparison]::OrdinalIgnoreCase
            )
        )
    } catch {
        return $false
    }
}

function Get-GrandeAlphaOwnedRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$LifecyclePath,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Launcher,
        [Parameter(Mandatory = $true)][string]$PythonLauncher,
        [Parameter(Mandatory = $true)][string]$PythonProcessExecutable,
        [Parameter(Mandatory = $true)][string]$PowerShellExe,
        [Parameter(Mandatory = $true)][string]$ActionArguments,
        [Parameter(Mandatory = $true)][string]$CurrentUserSid,
        [Parameter(Mandatory = $true)][string]$TaskName
    )

    $Result = [ordered]@{
        state = 'NONE'
        detail = 'No scheduled-shadow lifecycle record exists.'
        record = $null
        wrapper = $null
        child = $null
        wrapper_identity_valid = $false
        child_identity_valid = $false
    }
    try {
        $Record = Read-GrandeAlphaJsonFile $LifecyclePath
    } catch {
        $Result.state = 'INVALID_RECORD'
        $Result.detail = "Lifecycle record could not be parsed: $($_.Exception.Message)"
        return [pscustomobject]$Result
    }
    if ($null -eq $Record) {
        return [pscustomobject]$Result
    }
    $Result.record = $Record
    if (-not (Test-GrandeAlphaLifecycleRecord `
        -Record $Record `
        -ExpectedProjectRoot $ProjectRoot `
        -ExpectedLauncher $Launcher `
        -ExpectedPythonLauncher $PythonLauncher `
        -ExpectedPythonProcessExecutable $PythonProcessExecutable `
        -ExpectedOwnerSid $CurrentUserSid `
        -ExpectedTaskName $TaskName
    )) {
        $Result.state = 'INVALID_RECORD'
        $Result.detail = 'Lifecycle record does not match this current-user installation.'
        return [pscustomobject]$Result
    }

    $Wrapper = Get-GrandeAlphaProcessSnapshot ([int]$Record.wrapper_process_id)
    $Result.wrapper = $Wrapper
    if ($null -ne $Wrapper) {
        $Result.wrapper_identity_valid = Test-GrandeAlphaProcessIdentity `
            -Snapshot $Wrapper `
            -ExpectedProcessId ([int]$Record.wrapper_process_id) `
            -ExpectedStartedAtUtc ([string]$Record.wrapper_started_at_utc) `
            -ExpectedExecutable $PowerShellExe `
            -ExpectedArguments $ActionArguments `
            -ExpectedOwnerSid $CurrentUserSid
    }

    if ($null -eq $Record.child_process_id) {
        if ($Result.wrapper_identity_valid) {
            $Result.state = 'STARTING'
            $Result.detail = "Owned wrapper is $($Record.state); no child PID is recorded yet."
        } elseif ($null -eq $Wrapper) {
            $Result.state = 'STOPPED'
            $Result.detail = 'Lifecycle record has no child and its wrapper has exited.'
        } else {
            $Result.state = 'UNVERIFIED'
            $Result.detail = 'Lifecycle wrapper PID exists but its identity does not match.'
        }
        return [pscustomobject]$Result
    }

    $Child = Get-GrandeAlphaProcessSnapshot ([int]$Record.child_process_id)
    $Result.child = $Child
    if ($null -eq $Child) {
        if ($Result.wrapper_identity_valid) {
            $Result.state = 'RETRYING'
            $Result.detail = "Owned wrapper is $($Record.state); its recorded child has exited."
        } else {
            $Result.state = 'STOPPED'
            $Result.detail = 'The recorded child and wrapper have exited.'
        }
        return [pscustomobject]$Result
    }
    $Result.child_identity_valid = Test-GrandeAlphaProcessIdentity `
        -Snapshot $Child `
        -ExpectedProcessId ([int]$Record.child_process_id) `
        -ExpectedStartedAtUtc ([string]$Record.child_started_at_utc) `
        -ExpectedExecutable $PythonLauncher `
        -ExpectedArguments '-m grande_alpha.app --auto-shadow' `
        -ExpectedOwnerSid $CurrentUserSid
    if (-not $Result.child_identity_valid) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Recorded child PID exists but start time, owner, executable, or argv does not match.'
        return [pscustomobject]$Result
    }
    try {
        $ChildStartedAt = [datetimeoffset]::Parse(
            [string]$Child.started_at_utc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $WrapperStartedAt = [datetimeoffset]::Parse(
            [string]$Record.wrapper_started_at_utc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        $ProcessTreeValid = (
            [int]$Child.parent_process_id -eq [int]$Record.wrapper_process_id -and
            $ChildStartedAt.UtcDateTime.Ticks -ge $WrapperStartedAt.UtcDateTime.Ticks
        )
    } catch {
        $ProcessTreeValid = $false
    }
    if (-not $ProcessTreeValid) {
        $Result.state = 'UNVERIFIED'
        $Result.detail = 'Recorded child is not a time-ordered direct child of the recorded wrapper PID.'
    } elseif ($Result.wrapper_identity_valid) {
        $Result.state = 'OWNED'
        $Result.detail = 'Wrapper and child identities match the current-user lifecycle record.'
    } else {
        $Result.state = 'ORPHANED'
        $Result.detail = 'The exact recorded child is alive after its owning wrapper exited.'
    }
    return [pscustomobject]$Result
}
