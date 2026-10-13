"""The .bak files the editor leaves behind, and getting one back.

Every write copies the save to `<save>.<timestamp>.bak` first. That is the
whole safety net, and until now nothing in the app showed it - you had to find
the files in Explorer and rename them by hand.

Restoring makes a backup of the *current* file first, so undoing a restore is
itself possible.
"""
import glob
import os
import re
import shutil
import time

STAMP = re.compile(r'\.(\d{8})-(\d{6})\.bak$')


def backups_for(save_path):
    """Every backup of one save, newest first."""
    out = []
    for path in glob.glob(glob.escape(save_path) + '.*.bak'):
        st = os.stat(path)
        m = STAMP.search(path)
        when = None
        if m:
            try:
                when = time.strftime(
                    '%d %b %Y, %H:%M:%S',
                    time.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S'))
            except ValueError:
                when = None
        out.append({
            'path': path,
            'name': os.path.basename(path),
            'size': st.st_size,
            'mtime': st.st_mtime,
            'when': when or time.strftime('%d %b %Y, %H:%M:%S',
                                          time.localtime(st.st_mtime)),
        })
    out.sort(key=lambda d: d['mtime'], reverse=True)
    return out


def describe(path):
    """A one-line summary of what is inside a backup, so you can tell them
    apart without loading the game."""
    try:
        import ja3_save
        s = ja3_save.Save(path)
        meta = s.meta_info()
        mercs = s.player_units()
        return {
            'ok': True,
            'save': meta.get('displayname'),
            'sector': meta.get('map'),
            'money': s.money(),
            'mercs': len(mercs),
            'names': [s.merc_info(u)['nick'] for u in mercs[:6]],
        }
    except Exception as exc:                                     # noqa: BLE001
        return {'ok': False, 'error': str(exc)}


def _belongs_to(backup_path, save_path):
    """Both paths come from different places - glob output, a UI field, a
    command line - so compare them normalised rather than as raw strings."""
    a = os.path.normcase(os.path.abspath(backup_path))
    b = os.path.normcase(os.path.abspath(save_path))
    return a.startswith(b) and a.endswith('.bak')


def restore(backup_path, save_path):
    """Put a backup back, keeping the current file as a new backup."""
    if not os.path.isfile(backup_path):
        return False, 'that backup is gone'
    if not _belongs_to(backup_path, save_path):
        return False, 'that backup does not belong to this save'
    try:
        safety = None
        if os.path.isfile(save_path):
            safety = '%s.%s.bak' % (save_path, time.strftime('%Y%m%d-%H%M%S'))
            shutil.copy2(save_path, safety)
        tmp = save_path + '.restoring'
        shutil.copy2(backup_path, tmp)
        os.replace(tmp, save_path)
    except OSError as exc:
        return False, 'could not restore: %s' % exc
    return True, {'restored_from': backup_path, 'previous_kept_as': safety}


def delete(backup_path, save_path):
    if not _belongs_to(backup_path, save_path):
        return False, 'that is not a backup of this save'
    try:
        os.remove(backup_path)
    except OSError as exc:
        return False, str(exc)
    return True, backup_path


def prune(save_path, keep=10):
    """Delete all but the newest `keep` backups of one save."""
    rows = backups_for(save_path)
    removed = []
    for row in rows[keep:]:
        try:
            os.remove(row['path'])
            removed.append(row['name'])
        except OSError:
            pass
    return removed
