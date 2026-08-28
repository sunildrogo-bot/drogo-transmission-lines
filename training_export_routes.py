"""Admin-only COCO/YOLO dataset exports from reviewed tower annotations."""
import csv
import io
import json
import os
import random
import shutil
import threading
import uuid
import zipfile
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, session
from PIL import Image
from werkzeug.utils import secure_filename

from models import db, Project, TowerDefect, TowerInspectionStatus, TowerPhoto


training_export_bp = Blueprint('training_export_bp', __name__)
_job_lock = threading.Lock()


def _admin_guard():
    if 'user_id' not in session:
        return jsonify({'error': 'Please sign in.'}), 401
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Admin access required.'}), 403
    return None


def _export_root(app=None):
    app = app or current_app
    path = os.path.join(app.instance_path, 'training_exports')
    os.makedirs(path, exist_ok=True)
    return path


def _job_path(job_id, app=None):
    return os.path.join(_export_root(app), f'{job_id}.json')


def _class_config_path(app=None):
    app = app or current_app
    os.makedirs(app.instance_path, exist_ok=True)
    return os.path.join(app.instance_path, 'training_class_map.json')


def _read_class_config(app=None):
    try:
        with open(_class_config_path(app), encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data.get('mappings'), dict) or not isinstance(data.get('class_order'), list):
            raise ValueError('Invalid class map')
        return data
    except (OSError, ValueError, TypeError):
        return {'version': 1, 'class_order': [], 'mappings': {}}


def _write_class_config(data, app=None):
    path = _class_config_path(app)
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    with _job_lock:
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2)
        os.replace(temporary, path)


def _read_job(job_id, app=None):
    try:
        with open(_job_path(job_id, app), encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write_job(job, app=None):
    path = _job_path(job['id'], app)
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    with _job_lock:
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(job, handle, indent=2)
        os.replace(temporary, path)


def _absolute_photo_path(photo, app=None):
    app = app or current_app
    stored = (photo.display_image_path() or '').replace('\\', '/').lstrip('/')
    if stored.startswith('static/'):
        stored = stored[len('static/'):]
    candidate = os.path.abspath(os.path.join(app.static_folder, stored))
    static_root = os.path.abspath(app.static_folder)
    if os.path.commonpath([candidate, static_root]) != static_root:
        return ''
    return candidate


def _canonical_label(value):
    return ' '.join((value or '').strip().split())


def _mapped_label(source_label, config):
    source = _canonical_label(source_label)
    if not source:
        return ''
    rule = config.get('mappings', {}).get(source.casefold())
    if rule and rule.get('enabled') is False:
        return ''
    return _canonical_label(rule.get('target')) if rule else source


def _safe_dataset_name(value):
    safe = secure_filename(value or '')[:80]
    return safe or 'drogo_training_dataset'


def _parse_selection(payload):
    try:
        project_id = int(payload.get('project_id'))
    except (TypeError, ValueError):
        raise ValueError('Select a project.')
    line_ids = []
    for value in payload.get('line_ids') or []:
        try:
            line_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    export_format = payload.get('format') or 'yolo-detection'
    if export_format not in {'yolo-detection', 'coco-detection'}:
        raise ValueError('Unsupported dataset format.')
    return {
        'project_id': project_id,
        'line_ids': sorted(set(line_ids)),
        'format': export_format,
        'inspection_done_only': payload.get('inspection_done_only', True) is not False,
        'include_negatives': bool(payload.get('include_negatives', True)),
        'negative_ratio': min(3.0, max(0.0, float(payload.get('negative_ratio', 1.0)))),
        'labels_only': bool(payload.get('labels_only', False)),
        'split_seed': int(payload.get('split_seed') or 42),
    }


def _split_towers(tower_keys, seed):
    keys = sorted(set(tower_keys))
    random.Random(seed).shuffle(keys)
    count = len(keys)
    if count < 2:
        return {key: 'train' for key in keys}
    if count == 2:
        return {keys[0]: 'train', keys[1]: 'val'}
    test_count = max(1, round(count * .10))
    val_count = max(1, round(count * .20))
    if test_count + val_count >= count:
        test_count, val_count = 1, 1
    result = {}
    for index, key in enumerate(keys):
        result[key] = 'test' if index < test_count else ('val' if index < test_count + val_count else 'train')
    return result


def _shape_bounds(defect):
    try:
        points = json.loads(defect.shape_coords or '[]')
        xs = [max(0.0, min(100.0, float(point['x']))) for point in points]
        ys = [max(0.0, min(100.0, float(point['y']))) for point in points]
    except (TypeError, ValueError, KeyError):
        return None
    if len(xs) < 2:
        return None
    if defect.shape_type == 'circle' and len(xs) == 2:
        radius = ((xs[1] - xs[0]) ** 2 + (ys[1] - ys[0]) ** 2) ** .5
        left, right = max(0.0, xs[0] - radius), min(100.0, xs[0] + radius)
        top, bottom = max(0.0, ys[0] - radius), min(100.0, ys[0] + radius)
    else:
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    if right - left < .01 or bottom - top < .01:
        return None
    return left, top, right, bottom


def _build_plan(selection):
    project = db.session.get(Project, selection['project_id'])
    if not project:
        raise ValueError('Project not found.')
    project_line_ids = [line.id for division in project.divisions for line in division.lines]
    selected_line_ids = selection['line_ids'] or project_line_ids
    selected_line_ids = sorted(set(selected_line_ids) & set(project_line_ids))
    if not selected_line_ids:
        raise ValueError('The selected project has no matching lines.')

    done_keys = {
        (row.line_id, row.tower_label)
        for row in TowerInspectionStatus.query.filter(
            TowerInspectionStatus.line_id.in_(selected_line_ids),
            TowerInspectionStatus.inspection_done.is_(True),
        ).all()
    }
    photos = TowerPhoto.query.filter(TowerPhoto.line_id.in_(selected_line_ids)).order_by(TowerPhoto.id).all()
    class_config = _read_class_config()
    positives, negative_candidates, warnings = [], [], []
    label_lookup = {}
    missing_files = invalid_annotations = empty_labels = 0
    for photo in photos:
        if photo.is_thermal_image():
            continue
        tower_key = (photo.line_id, photo.tower_label)
        if selection['inspection_done_only'] and tower_key not in done_keys:
            continue
        path = _absolute_photo_path(photo)
        if not path or not os.path.isfile(path):
            missing_files += 1
            continue
        annotations = []
        for defect in photo.defects:
            if defect.deleted_at:
                continue
            label = _mapped_label(defect.defect_type, class_config)
            if not label:
                empty_labels += 1
                continue
            bounds = _shape_bounds(defect)
            if not bounds:
                invalid_annotations += 1
                continue
            key = label.casefold()
            label_lookup.setdefault(key, label)
            annotations.append({'defect_id': defect.id, 'label_key': key, 'bounds': bounds,
                                'component': defect.component_name or '', 'severity': defect.severity or 'Minor',
                                'resolution_status': defect.resolution_status or 'Open'})
        item = {'photo_id': photo.id, 'line_id': photo.line_id, 'tower_label': photo.tower_label,
                'tower_key': tower_key, 'path': path,
                'source_path': (photo.display_image_path() or '').replace('\\', '/'),
                'annotations': annotations}
        if annotations:
            positives.append(item)
        elif selection['include_negatives'] and tower_key in done_keys:
            negative_candidates.append(item)

    if missing_files:
        warnings.append(f'{missing_files} image file(s) are missing and will be skipped.')
    if invalid_annotations:
        warnings.append(f'{invalid_annotations} invalid annotation(s) will be skipped.')
    if empty_labels:
        warnings.append(f'{empty_labels} annotation(s) have no defect type and will be skipped.')
    if not positives:
        raise ValueError('No valid RGB defect annotations match this selection.')

    rng = random.Random(selection['split_seed'])
    rng.shuffle(negative_candidates)
    negative_limit = int(round(len(positives) * selection['negative_ratio']))
    negatives = negative_candidates[:negative_limit]
    items = positives + negatives
    source_bytes = sum(os.path.getsize(item['path']) for item in items)
    available_bytes = shutil.disk_usage(_export_root()).free
    if not selection['labels_only'] and source_bytes > available_bytes * .95:
        warnings.append('The selected images may exceed currently available export disk space.')
    split_map = _split_towers([item['tower_key'] for item in items], selection['split_seed'])
    configured_order = [str(value).casefold() for value in class_config.get('class_order', [])]
    ordered_keys = [key for key in configured_order if key in label_lookup]
    ordered_keys.extend(key for key in sorted(label_lookup) if key not in ordered_keys)
    labels = [label_lookup[key] for key in ordered_keys]
    class_ids = {key: index for index, key in enumerate(ordered_keys)}
    class_counts = {label: 0 for label in labels}
    split_counts = {'train': 0, 'val': 0, 'test': 0}
    annotation_count = 0
    for item in items:
        item['split'] = split_map[item['tower_key']]
        split_counts[item['split']] += 1
        for annotation in item['annotations']:
            annotation['class_id'] = class_ids[annotation['label_key']]
            class_counts[label_lookup[annotation['label_key']]] += 1
            annotation_count += 1
    rare = [label for label, count in class_counts.items() if count < 20]
    if rare:
        warnings.append(f'{len(rare)} class(es) have fewer than 20 annotations.')
    return {
        'project': project, 'selection': selection, 'items': items, 'labels': labels,
        'class_counts': class_counts, 'split_counts': split_counts, 'warnings': warnings,
        'positive_images': len(positives), 'negative_images': len(negatives),
        'annotation_count': annotation_count,
        'source_bytes': source_bytes, 'available_bytes': available_bytes,
    }


def _preview_dict(plan):
    return {'project': plan['project'].name, 'image_count': len(plan['items']),
            'positive_images': plan['positive_images'], 'negative_images': plan['negative_images'],
            'annotation_count': plan['annotation_count'], 'class_count': len(plan['labels']),
            'classes': plan['class_counts'], 'splits': plan['split_counts'],
            'source_bytes': plan['source_bytes'], 'warnings': plan['warnings']}


def _arcname(item, split):
    base = secure_filename(os.path.basename(item['path'])) or f"photo_{item['photo_id']}.jpg"
    return f"images/{split}/p{item['photo_id']}_{base}"


def _image_size(path):
    with Image.open(path) as image:
        return image.size


def _manifest_csv(plan, image_records):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['photo_id', 'line_id', 'tower_label', 'split', 'image_file', 'source_image_path', 'annotation_count', 'negative'])
    for item, image_name in image_records:
        writer.writerow([item['photo_id'], item['line_id'], item['tower_label'], item['split'], image_name, item['source_path'],
                         len(item['annotations']), 'yes' if not item['annotations'] else 'no'])
    return output.getvalue()


def _run_export(app, job_id, selection, actor):
    with app.app_context():
        job = _read_job(job_id, app)
        try:
            job.update(status='Preparing', progress=2)
            _write_job(job, app)
            plan = _build_plan(selection)
            if not selection['labels_only'] and plan['source_bytes'] > plan['available_bytes'] * .95:
                raise ValueError('Not enough free disk space to create this complete dataset ZIP. Use Labels only or free disk space.')
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            dataset_name = _safe_dataset_name(f"{plan['project'].name}_{selection['format']}_{timestamp}")
            zip_path = os.path.join(_export_root(app), f'{job_id}_{dataset_name}.zip')
            coco = {'images': [], 'annotations': [], 'categories': [
                {'id': index + 1, 'name': label} for index, label in enumerate(plan['labels'])
            ]}
            image_records = []
            annotation_id = 1
            total = max(1, len(plan['items']))
            with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for index, item in enumerate(plan['items'], 1):
                    width, height = _image_size(item['path'])
                    image_name = _arcname(item, item['split'])
                    if not selection['labels_only']:
                        archive.write(item['path'], image_name)
                    image_records.append((item, image_name))
                    if selection['format'] == 'yolo-detection':
                        lines = []
                        for ann in item['annotations']:
                            left, top, right, bottom = ann['bounds']
                            lines.append(f"{ann['class_id']} {(left + right) / 200:.6f} {(top + bottom) / 200:.6f} {(right-left) / 100:.6f} {(bottom-top) / 100:.6f}")
                        label_name = os.path.splitext(os.path.basename(image_name))[0] + '.txt'
                        archive.writestr(f"labels/{item['split']}/{label_name}", '\n'.join(lines) + ('\n' if lines else ''))
                    else:
                        coco['images'].append({'id': item['photo_id'], 'file_name': image_name,
                                               'width': width, 'height': height, 'split': item['split']})
                        for ann in item['annotations']:
                            left, top, right, bottom = ann['bounds']
                            x, y = width * left / 100, height * top / 100
                            box_w, box_h = width * (right-left) / 100, height * (bottom-top) / 100
                            coco['annotations'].append({'id': annotation_id, 'image_id': item['photo_id'],
                                'category_id': ann['class_id'] + 1, 'bbox': [x, y, box_w, box_h],
                                'area': box_w * box_h, 'iscrowd': 0, 'segmentation': []})
                            annotation_id += 1
                    job['progress'] = 5 + int(index / total * 85)
                    if index == total or index % 10 == 0:
                        _write_job(job, app)
                if selection['format'] == 'yolo-detection':
                    yaml = "path: .\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n" + ''.join(
                        f"  {index}: {json.dumps(label)}\n" for index, label in enumerate(plan['labels']))
                    archive.writestr('data.yaml', yaml)
                    archive.writestr('classes.txt', '\n'.join(plan['labels']) + '\n')
                else:
                    for split in ('train', 'val', 'test'):
                        image_ids = {row['id'] for row in coco['images'] if row['split'] == split}
                        payload = {'images': [{k: v for k, v in row.items() if k != 'split'} for row in coco['images'] if row['id'] in image_ids],
                                   'annotations': [row for row in coco['annotations'] if row['image_id'] in image_ids],
                                   'categories': coco['categories']}
                        archive.writestr(f'annotations/instances_{split}.json', json.dumps(payload, indent=2))
                archive.writestr('manifest.csv', _manifest_csv(plan, image_records))
                archive.writestr('dataset_manifest.json', json.dumps({
                    'name': dataset_name, 'created_at': datetime.utcnow().isoformat() + 'Z', 'created_by': actor,
                    'format': selection['format'], 'image_domain': 'RGB', 'split_unit': 'tower',
                    'split_seed': selection['split_seed'], 'labels_only': selection['labels_only'],
                    'summary': _preview_dict(plan), 'classes': plan['labels'],
                    'class_mapping': _read_class_config(app),
                }, indent=2))
                archive.writestr('class_mapping.json', json.dumps(_read_class_config(app), indent=2))
                archive.writestr('README.txt', 'Generated from DROGO reviewed RGB tower annotations.\nSplits are grouped by complete tower to reduce data leakage.\n')
            job.update(status='Completed', progress=100, finished_at=datetime.utcnow().isoformat() + 'Z',
                       file_name=os.path.basename(zip_path), download_name=f'{dataset_name}.zip',
                       file_size=os.path.getsize(zip_path), summary=_preview_dict(plan), error='')
        except Exception as exc:
            app.logger.exception('Training dataset export %s failed', job_id)
            job.update(status='Failed', finished_at=datetime.utcnow().isoformat() + 'Z', error=str(exc))
        _write_job(job, app)


@training_export_bp.route('/training-datasets')
def training_datasets_page():
    guard = _admin_guard()
    if guard:
        return guard
    return render_template('training_datasets.html', user_name=session.get('user_name', 'Admin'))


@training_export_bp.route('/api/training-datasets/options')
def training_dataset_options():
    guard = _admin_guard()
    if guard:
        return guard
    projects = []
    for project in Project.query.order_by(Project.name).all():
        divisions = []
        for division in sorted(project.divisions, key=lambda row: row.name.casefold()):
            divisions.append({'id': division.id, 'name': division.name, 'lines': [
                {'id': line.id, 'name': line.name} for line in sorted(division.lines, key=lambda row: row.name.casefold())
            ]})
        projects.append({'id': project.id, 'name': project.name, 'module': project.module, 'divisions': divisions})
    return jsonify({'projects': projects})


@training_export_bp.route('/api/training-datasets/classes')
def training_dataset_classes():
    guard = _admin_guard()
    if guard:
        return guard
    config = _read_class_config()
    observed = {}
    for defect in TowerDefect.query.filter(TowerDefect.deleted_at.is_(None)).order_by(TowerDefect.id).all():
        source = _canonical_label(defect.defect_type)
        if not source:
            continue
        key = source.casefold()
        entry = observed.setdefault(key, {'source': source, 'annotation_count': 0})
        entry['annotation_count'] += 1
    rows = []
    for key, entry in sorted(observed.items(), key=lambda item: item[1]['source'].casefold()):
        rule = config.get('mappings', {}).get(key, {})
        rows.append({'source': entry['source'], 'target': _canonical_label(rule.get('target')) or entry['source'],
                     'enabled': rule.get('enabled') is not False, 'annotation_count': entry['annotation_count']})
    return jsonify({'classes': rows, 'class_order': config.get('class_order', [])})


@training_export_bp.route('/api/training-datasets/classes', methods=['PUT'])
def update_training_dataset_classes():
    guard = _admin_guard()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    rows = payload.get('classes')
    if not isinstance(rows, list):
        return jsonify({'error': 'classes must be a list.'}), 400
    existing = _read_class_config()
    mappings, requested_targets = {}, []
    for row in rows:
        source = _canonical_label(row.get('source') if isinstance(row, dict) else '')
        target = _canonical_label(row.get('target') if isinstance(row, dict) else '')
        enabled = bool(row.get('enabled', True)) if isinstance(row, dict) else False
        if not source:
            continue
        if enabled and not target:
            return jsonify({'error': f'Enter a training class for {source} or exclude it.'}), 400
        if len(target) > 100:
            return jsonify({'error': f'Training class for {source} is too long.'}), 400
        mappings[source.casefold()] = {'source': source, 'target': target or source, 'enabled': enabled}
        if enabled and target.casefold() not in {value.casefold() for value in requested_targets}:
            requested_targets.append(target)
    requested_keys = {value.casefold() for value in requested_targets}
    class_order, seen_targets = [], set()
    for target in list(existing.get('class_order', [])) + requested_targets:
        key = str(target).casefold()
        if key in requested_keys and key not in seen_targets:
            seen_targets.add(key)
            class_order.append(next(value for value in requested_targets if value.casefold() == key))
    config = {'version': 1, 'updated_at': datetime.utcnow().isoformat() + 'Z',
              'updated_by': session.get('user_name', 'Admin'),
              'class_order': class_order, 'mappings': mappings}
    _write_class_config(config)
    return jsonify({'saved': True, 'class_order': class_order})


@training_export_bp.route('/api/training-datasets/preview', methods=['POST'])
def training_dataset_preview():
    guard = _admin_guard()
    if guard:
        return guard
    try:
        plan = _build_plan(_parse_selection(request.get_json(silent=True) or {}))
        return jsonify(_preview_dict(plan))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400


@training_export_bp.route('/api/training-datasets/exports', methods=['POST'])
def create_training_dataset_export():
    guard = _admin_guard()
    if guard:
        return guard
    try:
        selection = _parse_selection(request.get_json(silent=True) or {})
        preview = _preview_dict(_build_plan(selection))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    job_id = uuid.uuid4().hex
    job = {'id': job_id, 'status': 'Queued', 'progress': 0, 'created_at': datetime.utcnow().isoformat() + 'Z',
           'created_by': session.get('user_name', 'Admin'), 'selection': selection, 'summary': preview, 'error': ''}
    _write_job(job)
    app = current_app._get_current_object()
    threading.Thread(target=_run_export, args=(app, job_id, selection, job['created_by']), daemon=True).start()
    return jsonify(job), 202


@training_export_bp.route('/api/training-datasets/exports')
def list_training_dataset_exports():
    guard = _admin_guard()
    if guard:
        return guard
    jobs = []
    for filename in os.listdir(_export_root()):
        if filename.endswith('.json'):
            job = _read_job(filename[:-5])
            if job:
                jobs.append(job)
    jobs.sort(key=lambda row: row.get('created_at', ''), reverse=True)
    return jsonify({'exports': jobs[:50]})


@training_export_bp.route('/api/training-datasets/exports/<job_id>')
def training_dataset_export_status(job_id):
    guard = _admin_guard()
    if guard:
        return guard
    job = _read_job(job_id)
    return jsonify(job) if job else (jsonify({'error': 'Export not found.'}), 404)


@training_export_bp.route('/api/training-datasets/exports/<job_id>/download')
def download_training_dataset_export(job_id):
    guard = _admin_guard()
    if guard:
        return guard
    job = _read_job(job_id)
    if not job or job.get('status') != 'Completed' or not job.get('file_name'):
        return jsonify({'error': 'Export is not ready.'}), 404
    path = os.path.abspath(os.path.join(_export_root(), job['file_name']))
    if os.path.commonpath([path, os.path.abspath(_export_root())]) != os.path.abspath(_export_root()) or not os.path.isfile(path):
        return jsonify({'error': 'Export file is missing.'}), 404
    return send_file(path, as_attachment=True, download_name=job.get('download_name') or 'training_dataset.zip')


@training_export_bp.route('/api/training-datasets/exports/<job_id>', methods=['DELETE'])
def delete_training_dataset_export(job_id):
    guard = _admin_guard()
    if guard:
        return guard
    job = _read_job(job_id)
    if not job:
        return jsonify({'error': 'Export not found.'}), 404
    if job.get('status') not in {'Completed', 'Failed'}:
        return jsonify({'error': 'Wait for the export to finish before deleting it.'}), 409
    root = os.path.abspath(_export_root())
    for filename in (job.get('file_name'), f'{job_id}.json'):
        if not filename:
            continue
        path = os.path.abspath(os.path.join(root, filename))
        if os.path.commonpath([path, root]) == root and os.path.isfile(path):
            os.remove(path)
    return jsonify({'deleted': True})
