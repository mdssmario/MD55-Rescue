"""Unidades donde puede haber una tarjeta (Windows y macOS)."""
import os
import plistlib
import subprocess
import sys


def _win_drives():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    system = os.environ.get('SystemDrive', 'C:').upper()
    mask = k32.GetLogicalDrives()
    out = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = '%s:' % chr(65 + i)
        root = letter + '\\'
        dtype = k32.GetDriveTypeW(root)
        if dtype not in (2, 3):          # extraíble o fijo (las cámaras en MSC salen como fijo)
            continue
        label = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        if not k32.GetVolumeInformationW(root, label, 261, None, None, None, fs, 261):
            label.value, fs.value = '', '?'
        total = ctypes.c_ulonglong(0)
        k32.GetDiskFreeSpaceExW(root, None, ctypes.byref(total), None)
        out.append({
            'id': letter,
            'name': label.value or ('Unidad extraíble' if dtype == 2 else 'Disco'),
            'fs': fs.value,
            'size': total.value,
            'removable': dtype == 2,
            'system': letter == system,
        })
    return out


def _mac_drives():
    out = []
    try:
        raw = subprocess.run(['diskutil', 'list', '-plist', 'external', 'physical'],
                             capture_output=True, check=True).stdout
        disks = plistlib.loads(raw).get('WholeDisks', [])
    except (OSError, subprocess.CalledProcessError, plistlib.InvalidFileException):
        return out
    for d in disks:
        try:
            info = plistlib.loads(subprocess.run(['diskutil', 'info', '-plist', d],
                                                 capture_output=True, check=True).stdout)
        except (OSError, subprocess.CalledProcessError):
            continue
        out.append({
            'id': '/dev/' + d,
            'name': info.get('MediaName') or info.get('IORegistryEntryName') or d,
            'fs': info.get('FilesystemName', ''),
            'size': info.get('TotalSize') or info.get('Size') or 0,
            'removable': bool(info.get('RemovableMedia') or info.get('Ejectable')),
            'system': False,
        })
    return out


def mount_points(disk):
    """Puntos de montaje de las particiones de un disco de macOS (/dev/diskN)."""
    name = os.path.basename(disk).replace('rdisk', 'disk')
    try:
        raw = subprocess.run(['diskutil', 'list', '-plist', name], capture_output=True, check=True).stdout
        data = plistlib.loads(raw)
    except (OSError, subprocess.CalledProcessError, plistlib.InvalidFileException):
        return []
    out = []
    for d in data.get('AllDisksAndPartitions', []):
        for part in [d] + d.get('Partitions', []) + d.get('APFSVolumes', []):
            if part.get('MountPoint'):
                out.append(part['MountPoint'])
    return out


def list_drives():
    if sys.platform == 'win32':
        return _win_drives()
    if sys.platform == 'darwin':
        return _mac_drives()
    return []


def is_device(source):
    s = source.strip()
    if sys.platform == 'win32':
        return len(s) <= 3 and s[1:2] == ':' or s.startswith('\\\\.\\')
    return s.startswith('/dev/')


def is_admin():
    if sys.platform == 'win32':
        import ctypes
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except OSError:
            return False
    return os.geteuid() == 0
