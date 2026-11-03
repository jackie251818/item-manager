#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
物品使用登记系统 - 以人员为主导
支持人员头像上传和物品图片上传
"""
import os
import sqlite3
import uuid
import io
import logging
import logging.handlers
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g, send_from_directory, send_file
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'item-manager-secret-key-2024')
app.config['DATABASE'] = os.environ.get('DB_PATH', '/app/data/items.db')
app.config['ADMIN_USERNAME'] = os.environ.get('ADMIN_USERNAME', 'admin')
app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD', 'hmt666688')
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_PATH', '/app/data/uploads')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB
app.config['SESSION_COOKIE_HTTPONLY'] = True   # 防 XSS 窃取 session
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # 防 CSRF 跨站携带

# ===== 日志配置 =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),     # stderr / systemd
        logging.handlers.RotatingFileHandler(
            os.path.join(os.path.dirname(app.config['DATABASE']), 'item-manager.log'),
            maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
        ) if os.path.isdir(os.path.dirname(app.config['DATABASE'])) else logging.NullHandler()
    ]
)
logger = logging.getLogger('item-manager')


@app.after_request
def no_cache_html(resp):
    """HTML/JSON 响应不缓存，静态资源保留默认缓存"""
    ct = resp.headers.get('Content-Type', '')
    if 'text/html' in ct or 'application/json' in ct:
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
    return resp

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
ALLOWED_UPLOAD_SUBDIRS = {'avatars', 'items', 'persons'}  # 上传子目录白名单

# 登录防暴力破解：内存字典 {username: [fail_count, unlock_timestamp]}
_login_failures = {}
MAX_LOGIN_FAILS = 5
LOGIN_LOCK_SECONDS = 60


# ============ CSRF 保护 ============
def _get_csrf_token():
    """从 session 取，没有就生成一个"""
    if '_csrf' not in session:
        session['_csrf'] = uuid.uuid4().hex
    return session['_csrf']


app.jinja_env.globals['csrf_token'] = _get_csrf_token  # 模板里直接 {{ csrf_token() }}


@app.before_request
def csrf_protect():
    """所有 POST 校验 CSRF token（login 路由跳过，因为还没 session）"""
    if request.method == 'POST':
        # login / logout / 静态文件 跳过
        if request.endpoint in ('login',) or request.endpoint is None:
            return
        expected = session.get('_csrf', '')
        # 从 form / header / JSON body 取
        token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token', '')
        if not token and request.is_json:
            try:
                token = request.get_json(silent=True).get('csrf_token', '')
            except Exception:
                pass
        if not token or token != expected:
            # JSON 请求返回 JSON 错误，form 请求返回 400 纯文本
            if request.headers.get('Accept', '').startswith('application/json'):
                return jsonify({'ok': False, 'msg': 'CSRF 校验失败，请刷新页面重试'}), 400
            return 'CSRF 校验失败，请刷新页面重试', 400



def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------- 数据库 ----------
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """初始化数据库"""
    db_path = app.config['DATABASE']
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'avatars'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'items'), exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 人员表
    c.execute('''
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            department TEXT,
            phone TEXT,
            avatar TEXT,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # 增加 avatar 字段（兼容老数据库）
    try:
        c.execute("ALTER TABLE persons ADD COLUMN avatar TEXT")
    except sqlite3.OperationalError:
        pass

    # 物品表
    c.execute('''
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            model TEXT,
            serial TEXT,
            image TEXT,
            total_count INTEGER DEFAULT 1,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # 增加 image 字段（兼容老数据库）
    try:
        c.execute("ALTER TABLE items ADD COLUMN image TEXT")
    except sqlite3.OperationalError:
        pass

    # 借用记录表
    c.execute('''
        CREATE TABLE IF NOT EXISTS borrows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            borrow_date TEXT NOT NULL,
            expected_return_date TEXT,
            actual_return_date TEXT,
            note TEXT,
            status TEXT DEFAULT 'borrowed',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        )
    ''')

    # 单件资产表
    c.execute('''
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            asset_no TEXT NOT NULL,
            image TEXT,
            note TEXT,
            status TEXT DEFAULT 'available',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        )
    ''')

    # borrows 表增加 asset_id 字段（兼容老数据）
    try:
        c.execute("ALTER TABLE borrows ADD COLUMN asset_id INTEGER")
    except sqlite3.OperationalError:
        pass

    # assets 表增加 model 和 serial 字段
    try:
        c.execute("ALTER TABLE assets ADD COLUMN model TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE assets ADD COLUMN serial TEXT")
    except sqlite3.OperationalError:
        pass

    # ===== 用户表（密码哈希存储）=====
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # 确保初始 admin 账号存在（从环境变量读明文密码，哈希后存 DB）
    admin_username = app.config['ADMIN_USERNAME']
    admin_password = app.config['ADMIN_PASSWORD']
    c.execute("SELECT id FROM users WHERE username=?", (admin_username,))
    if not c.fetchone():
        c.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                  (admin_username, generate_password_hash(admin_password)))
        logger.info('init_db 已创建初始管理员账号: %s', admin_username)

    # ===== 审计日志表 =====
    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # ===== 数据迁移 v1：把已有物品按 total_count 生成单件资产 =====
    asset_count = c.execute('SELECT COUNT(*) as c FROM assets').fetchone()['c']
    item_count = c.execute('SELECT COUNT(*) as c FROM items').fetchone()['c']

    if asset_count == 0 and item_count > 0:
        items = c.execute('SELECT * FROM items').fetchall()
        for item in items:
            for i in range(1, item['total_count'] + 1):
                asset_no = f"{item['name']}-{i:03d}"
                c.execute(
                    'INSERT INTO assets (item_id, asset_no, image, model, serial, status) VALUES (?, ?, ?, ?, ?, ?)',
                    (item['id'], asset_no, item['image'], item['model'], item['serial'], 'available')
                )

        # 把已有的借用记录分配到具体资产（按顺序分配）
        borrows = c.execute(
            "SELECT * FROM borrows WHERE status='borrowed' ORDER BY id"
        ).fetchall()

        for b in borrows:
            qty = b['quantity']
            # 找该物品下可用的资产
            assets = c.execute(
                "SELECT * FROM assets WHERE item_id=? AND status='available' ORDER BY id LIMIT ?",
                (b['item_id'], qty)
            ).fetchall()

            if len(assets) > 0:
                # 第一件用原来的 borrow 记录，关联 asset_id
                first_asset = assets[0]
                c.execute(
                    'UPDATE borrows SET asset_id=?, quantity=1 WHERE id=?',
                    (first_asset['id'], b['id'])
                )
                c.execute(
                    "UPDATE assets SET status='borrowed' WHERE id=?",
                    (first_asset['id'],)
                )

                # 如果数量>1，为多余的每件创建独立 borrow 记录
                for j in range(1, len(assets)):
                    a = assets[j]
                    c.execute('''
                        INSERT INTO borrows (person_id, item_id, asset_id, quantity,
                                            borrow_date, expected_return_date, note, status)
                        VALUES (?, ?, ?, 1, ?, ?, ?, 'borrowed')
                    ''', (b['person_id'], b['item_id'], a['id'],
                          b['borrow_date'], b['expected_return_date'], b['note']))
                    c.execute(
                        "UPDATE assets SET status='borrowed' WHERE id=?",
                        (a['id'],)
                    )

    # ===== 数据迁移 v2：把 items 表的 model/serial 复制到资产（已有资产但型号为空时） =====
    assets_without_model = c.execute(
        "SELECT COUNT(*) as c FROM assets WHERE model IS NULL OR model = ''"
    ).fetchone()['c']

    if assets_without_model > 0:
        c.execute('''
            UPDATE assets
            SET model = (SELECT model FROM items WHERE items.id = assets.item_id),
                serial = (SELECT serial FROM items WHERE items.id = assets.item_id)
            WHERE model IS NULL OR model = ''
        ''')

    conn.commit()
    conn.close()


def save_uploaded_file(file_obj, subfolder):
    """保存上传的图片，返回文件名（含子目录路径穿越防护）"""
    if file_obj and file_obj.filename and allowed_file(file_obj.filename):
        # 子目录白名单（防 ../ 穿越）
        if subfolder not in ALLOWED_UPLOAD_SUBDIRS:
            subfolder = 'items'
        subfolder = os.path.basename(subfolder)  # 再保险：去路径
        ext = file_obj.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        save_dir = os.path.join(app.config['UPLOAD_FOLDER'], subfolder)
        os.makedirs(save_dir, exist_ok=True)
        file_path = os.path.join(save_dir, filename)
        # 最终保险：resolve 后必须在 UPLOAD_FOLDER 下
        real_path = os.path.realpath(file_path)
        if not real_path.startswith(os.path.realpath(app.config['UPLOAD_FOLDER'])):
            return None
        file_obj.save(real_path)
        return f"{subfolder}/{filename}"
    return None


# ---------- 登录验证 ----------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ---------- 图片签名 URL + 安全发送 ----------
def _safe_send_upload(filename):
    """路径穿越防护 + send_from_directory"""
    filename = os.path.normpath(filename).lstrip('./').lstrip('\\')
    if filename.startswith('..') or filename.startswith('/') or filename.startswith('\\'):
        return '非法路径', 400
    real_file = os.path.realpath(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    if not real_file.startswith(os.path.realpath(app.config['UPLOAD_FOLDER'])):
        return '非法路径', 400
    if not os.path.exists(real_file):
        return '文件不存在', 404
    return send_file(real_file)


def make_signed_upload_url(filename, expire_seconds=3600):
    """为 uploads 下的文件生成签名 URL（防直接枚举）"""
    import hmac, hashlib, time
    secret = app.secret_key.encode()
    expire_ts = int(time.time()) + expire_seconds
    sign = hmac.new(secret, f"{filename}|{expire_ts}".encode(), hashlib.sha256).hexdigest()
    return url_for('signed_upload', filename=filename, sig=sign, exp=expire_ts)


@app.route('/u/<path:filename>')
@login_required
def signed_upload(filename):
    """签名图片访问：校验 HMAC 签名 + 过期时间 + 防路径穿越"""
    import hmac, hashlib, time as _t
    sig = request.args.get('sig', '')
    exp = request.args.get('exp', '0')
    if not sig or not exp:
        # 无签名但有 login：放行（兼容旧模板）
        return _safe_send_upload(filename)
    secret = app.secret_key.encode()
    expected = hmac.new(secret, f"{filename}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return '签名无效', 403
    if int(exp) < int(_t.time()):
        return '链接已过期', 410
    return _safe_send_upload(filename)


# ---------- 审计日志 ----------
def _audit(action, target_type, target_id, detail=''):
    """往 audit_log 写一条（异常不阻塞主流程）"""
    try:
        db = get_db()
        db.execute('INSERT INTO audit_log (actor, action, target_type, target_id, detail) VALUES (?, ?, ?, ?, ?)',
                   (session.get('username', 'anonymous'), action, target_type, str(target_id), str(detail)[:500]))
        db.commit()
    except Exception:
        pass


# ---------- 图片访问 ----------
@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    return _safe_send_upload(filename)


# ---------- 路由 ----------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        now_ts = datetime.now().timestamp()

        # 1. 检查是否被锁
        fail_info = _login_failures.get(username)
        if fail_info and fail_info[1] > now_ts:
            remain = int(fail_info[1] - now_ts)
            flash('🔒 登录失败次数过多，请 {} 秒后再试'.format(remain), 'error')
            return render_template('login.html')

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            # 登录成功 → 清空失败计数
            _login_failures.pop(username, None)
            session['logged_in'] = True
            session['username'] = username
            session['is_admin'] = bool(user['is_admin'])
            return redirect(url_for('index'))
        else:
            # 登录失败 → 累计
            cnt = (_login_failures.get(username) or [0, 0])[0] + 1
            if cnt >= MAX_LOGIN_FAILS:
                unlock_ts = now_ts + LOGIN_LOCK_SECONDS
                _login_failures[username] = [cnt, unlock_ts]
                flash('🔒 登录失败次数过多，账号已锁定 {} 秒'.format(LOGIN_LOCK_SECONDS), 'error')
            else:
                _login_failures[username] = [cnt, 0]
                flash('用户名或密码错误（第 {} 次，{} 次后锁定）'.format(
                    cnt, MAX_LOGIN_FAILS), 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """首页 - 以人员为主导的视图"""
    db = get_db()

    # 获取所有人员
    persons = db.execute(
        'SELECT * FROM persons ORDER BY name'
    ).fetchall()

    # 按部门分组
    dept_groups = {}
    for p in persons:
        dept = p['department'] or '未分组'
        if dept not in dept_groups:
            dept_groups[dept] = []
        dept_groups[dept].append(p)

    # 部门排序（有名字的按字母/拼音，"未分组"放最后）
    sorted_depts = sorted(dept_groups.keys(), key=lambda x: (x == '未分组', x))

    # ===== 优化：一次 SQL 拉全所有人的借用中物品（消灭 N+1）=====
    all_borrows = db.execute('''
        SELECT b.*, i.name as item_name, i.category as item_category,
               i.image as item_image,
               a.asset_no as asset_no, a.image as asset_image,
               a.model as model, a.serial as serial
        FROM borrows b
        JOIN items i ON b.item_id = i.id
        LEFT JOIN assets a ON b.asset_id = a.id
        WHERE b.status = 'borrowed'
        ORDER BY b.person_id, b.borrow_date DESC
    ''').fetchall()
    person_borrows = {}
    for b in all_borrows:
        person_borrows.setdefault(b['person_id'], []).append(b)

    # 统计数据
    stats = {
        'person_count': len(persons),
        'item_count': db.execute('SELECT COUNT(*) as c FROM items').fetchone()['c'],
        'asset_count': db.execute('SELECT COUNT(*) as c FROM assets').fetchone()['c'],
        'borrowed_count': db.execute(
            "SELECT COUNT(*) as c FROM borrows WHERE status='borrowed'"
        ).fetchone()['c'],
    }

    return render_template('index.html',
                           persons=persons,
                           dept_groups=dept_groups,
                           sorted_depts=sorted_depts,
                           person_borrows=person_borrows,
                           stats=stats)


# ---------- 人员管理 ----------
@app.route('/persons')
@login_required
def persons():
    db = get_db()
    all_persons = db.execute(
        'SELECT * FROM persons ORDER BY name'
    ).fetchall()

    # 按部门分组
    dept_groups = {}
    for p in all_persons:
        dept = p['department'] or '未分组'
        if dept not in dept_groups:
            dept_groups[dept] = []
        dept_groups[dept].append(p)

    sorted_depts = sorted(dept_groups.keys(), key=lambda x: (x == '未分组', x))

    return render_template('persons.html',
                           persons=all_persons,
                           dept_groups=dept_groups,
                           sorted_depts=sorted_depts)


@app.route('/person/add', methods=['POST'])
@login_required
def add_person():
    name = request.form.get('name', '').strip()
    department = request.form.get('department', '').strip()
    phone = request.form.get('phone', '').strip()
    note = request.form.get('note', '').strip()

    if not name:
        flash('姓名不能为空', 'error')
        return redirect(url_for('persons'))

    # 处理头像上传
    avatar = None
    avatar_file = request.files.get('avatar')
    if avatar_file and avatar_file.filename:
        avatar = save_uploaded_file(avatar_file, 'avatars')

    db = get_db()
    try:
        db.execute(
            'INSERT INTO persons (name, department, phone, avatar, note) VALUES (?, ?, ?, ?, ?)',
            (name, department, phone, avatar, note)
        )
        db.commit()
        flash('人员添加成功', 'success')
    except sqlite3.IntegrityError:
        flash('该人员已存在', 'error')
    return redirect(url_for('persons'))


@app.route('/person/<int:pid>/edit', methods=['POST'])
@login_required
def edit_person(pid):
    name = request.form.get('name', '').strip()
    department = request.form.get('department', '').strip()
    phone = request.form.get('phone', '').strip()
    note = request.form.get('note', '').strip()
    delete_avatar = request.form.get('delete_avatar', '') == '1'

    db = get_db()

    # 获取当前头像
    current = db.execute('SELECT avatar FROM persons WHERE id=?', (pid,)).fetchone()
    avatar = current['avatar'] if current else None

    # 处理删除头像
    if delete_avatar and avatar:
        avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar)
        if os.path.exists(avatar_path):
            os.remove(avatar_path)
        avatar = None

    # 处理新头像上传
    avatar_file = request.files.get('avatar')
    if avatar_file and avatar_file.filename:
        new_avatar = save_uploaded_file(avatar_file, 'avatars')
        if new_avatar:
            # 删除旧头像
            if avatar:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], avatar)
                if os.path.exists(old_path):
                    os.remove(old_path)
            avatar = new_avatar

    db.execute(
        'UPDATE persons SET name=?, department=?, phone=?, avatar=?, note=? WHERE id=?',
        (name, department, phone, avatar, note, pid)
    )
    db.commit()
    flash('人员信息已更新', 'success')
    return redirect(url_for('persons'))


@app.route('/person/<int:pid>/delete', methods=['POST'])
@login_required
def delete_person(pid):
    db = get_db()
    # 检查是否有未归还的物品
    active = db.execute(
        "SELECT COUNT(*) as c FROM borrows WHERE person_id=? AND status='borrowed'",
        (pid,)
    ).fetchone()['c']
    if active > 0:
        flash('该人员还有未归还的物品，无法删除', 'error')
        return redirect(url_for('persons'))

    # 删除头像文件
    person = db.execute('SELECT name, avatar FROM persons WHERE id=?', (pid,)).fetchone()
    person_name = person['name'] if person else ''
    if person and person['avatar']:
        avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], person['avatar'])
        if os.path.exists(avatar_path):
            os.remove(avatar_path)

    db.execute('DELETE FROM persons WHERE id=?', (pid,))
    _audit('delete', 'person', pid, f'name={person_name}')
    db.commit()
    flash('人员已删除', 'success')
    return redirect(url_for('persons'))


# ---------- 物品管理 ----------
@app.route('/items')
@login_required
def items():
    db = get_db()
    search = request.args.get('q', '').strip()
    sql = 'SELECT * FROM items'
    params = []
    if search:
        sql += ' WHERE name LIKE ? OR category LIKE ? OR model LIKE ?'
        like = f'%{search}%'
        params.extend([like, like, like])
    sql += ' ORDER BY name'
    all_items = db.execute(sql, params).fetchall()

    # 计算每个物品的数量 —— 以 assets 表为唯一事实源（而非 items.total_count 静态字段）
    item_list = []
    for item in all_items:
        # 一次性聚合查询 assets 表，拿真实总数/已借/可用
        asset_stats = db.execute(
            '''SELECT COUNT(*) as total,
                      SUM(CASE WHEN status='borrowed' THEN 1 ELSE 0 END) as borrowed,
                      SUM(CASE WHEN status='available' THEN 1 ELSE 0 END) as available
               FROM assets WHERE item_id=?''',
            (item['id'],)
        ).fetchone()
        item_dict = dict(item)
        item_dict['total_count'] = asset_stats['total'] or 0
        item_dict['borrowed_count'] = asset_stats['borrowed'] or 0
        item_dict['available_count'] = asset_stats['available'] or 0

        # 获取该物品下的单件资产
        assets = db.execute(
            'SELECT * FROM assets WHERE item_id=? ORDER BY asset_no',
            (item['id'],)
        ).fetchall()
        item_dict['assets'] = assets

        item_list.append(item_dict)

    # 按分类分组
    cat_groups = {}
    for item in item_list:
        cat = item['category'] or '未分类'
        if cat not in cat_groups:
            cat_groups[cat] = []
        cat_groups[cat].append(item)

    sorted_cats = sorted(cat_groups.keys(), key=lambda x: (x == '未分类', x))

    return render_template('items.html',
                           items=item_list,
                           cat_groups=cat_groups,
                           sorted_cats=sorted_cats,
                           search=search)


@app.route('/item/add', methods=['POST'])
@login_required
def add_item():
    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()
    model = request.form.get('model', '').strip()
    serial = request.form.get('serial', '').strip()
    total_count = int(request.form.get('total_count', 1) or 1)
    note = request.form.get('note', '').strip()

    if not name:
        flash('物品名称不能为空', 'error')
        return redirect(url_for('items'))

    # 处理图片上传
    image = None
    image_file = request.files.get('image')
    if image_file and image_file.filename:
        image = save_uploaded_file(image_file, 'items')

    db = get_db()
    cursor = db.execute(
        'INSERT INTO items (name, category, model, serial, image, total_count, note) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (name, category, model, serial, image, total_count, note)
    )
    item_id = cursor.lastrowid
    db.commit()

    # 生成初始单件资产（型号和序列号留空，后续在单件资产中编辑）
    for i in range(1, total_count + 1):
        asset_no = f"{name}-{i:03d}"
        db.execute(
            'INSERT INTO assets (item_id, asset_no, image, status) VALUES (?, ?, ?, ?)',
            (item_id, asset_no, image, 'available')
        )
    db.commit()

    flash(f'物品添加成功，已生成 {total_count} 件资产', 'success')
    return redirect(url_for('items'))


@app.route('/item/<int:iid>/edit', methods=['POST'])
@login_required
def edit_item(iid):
    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()
    model = request.form.get('model', '').strip()
    serial = request.form.get('serial', '').strip()
    total_count = int(request.form.get('total_count', 1) or 1)
    note = request.form.get('note', '').strip()
    delete_image = request.form.get('delete_image', '') == '1'

    db = get_db()

    # 获取当前图片
    current = db.execute('SELECT image FROM items WHERE id=?', (iid,)).fetchone()
    image = current['image'] if current else None

    # 处理删除图片
    if delete_image and image:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image)
        if os.path.exists(image_path):
            os.remove(image_path)
        image = None

    # 处理新图片上传
    image_file = request.files.get('image')
    if image_file and image_file.filename:
        new_image = save_uploaded_file(image_file, 'items')
        if new_image:
            # 删除旧图片
            if image:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], image)
                if os.path.exists(old_path):
                    os.remove(old_path)
            image = new_image

    # === 硬拦截：不能把 total_count 改到小于 borrowed 数量 ===
    borrowed_cnt = db.execute(
        "SELECT COUNT(*) FROM assets WHERE item_id=? AND status='borrowed'", (iid,)).fetchone()[0]
    if total_count < borrowed_cnt:
        db.rollback()
        flash('❌ 数量不能小于已借出的 {} 件！当前已借出 {} 件，请先归还再减少数量。'.format(
            borrowed_cnt, borrowed_cnt), 'error')
        return redirect(url_for('items'))

    db.execute(
        'UPDATE items SET name=?, category=?, model=?, serial=?, image=?, total_count=?, note=? WHERE id=?',
        (name, category, model, serial, image, total_count, note, iid)
    )

    # 同步 assets 表与 total_count 对齐（只增 available，borrowed 不能删）
    cur_assets = db.execute("SELECT id, asset_no, status FROM assets WHERE item_id=? ORDER BY CAST(SUBSTR(asset_no, -3) AS INTEGER)", (iid,)).fetchall()
    cur_total = len(cur_assets)

    if total_count > cur_total:
        # 增加：生成 asset_no（取当前最大序号 +1 开始）
        import re as _re
        max_idx = 0
        for a in cur_assets:
            m = _re.search(r'(\d+)$', a['asset_no'])
            if m:
                max_idx = max(max_idx, int(m.group(1)))
        prefix = cur_assets[0]['asset_no'].rsplit('-', 1)[0] if cur_assets else name
        for i in range(1, total_count - cur_total + 1):
            max_idx += 1
            asset_no = '{}-{:03d}'.format(prefix, max_idx)
            db.execute("INSERT INTO assets (item_id, asset_no, status) VALUES (?, ?, 'available')",
                       (iid, asset_no))
    elif total_count < cur_total:
        # 删除多余：先删 available 的，保留 borrowed 的
        available_ids = [a['id'] for a in cur_assets if a['status'] == 'available']
        remove = cur_total - total_count
        if remove > len(available_ids):
            flash('警告：请求数量少于 borrowed 资产数，已保留所有借出的 {} 件'.format(
                cur_total - len(available_ids)), 'error')
            remove = len(available_ids)
        if remove > 0:
            for aid in available_ids[:remove]:
                db.execute("DELETE FROM assets WHERE id=?", (aid,))

    db.commit()
    flash('物品信息已更新', 'success')
    return redirect(url_for('items'))


@app.route('/item/<int:iid>/delete', methods=['POST'])
@login_required
def delete_item(iid):
    db = get_db()
    active = db.execute(
        "SELECT COUNT(*) as c FROM borrows WHERE item_id=? AND status='borrowed'",
        (iid,)
    ).fetchone()['c']
    if active > 0:
        flash('该物品还有未归还的记录，无法删除', 'error')
        return redirect(url_for('items'))

    # 删除图片文件
    item = db.execute('SELECT name, image FROM items WHERE id=?', (iid,)).fetchone()
    item_name = item['name'] if item else ''
    if item and item['image']:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], item['image'])
        if os.path.exists(image_path):
            os.remove(image_path)

    db.execute('DELETE FROM items WHERE id=?', (iid,))
    _audit('delete', 'item', iid, f'name={item_name}')
    db.commit()
    flash('物品已删除', 'success')
    return redirect(url_for('items'))


# ---------- 单件资产管理 ----------
@app.route('/asset/add', methods=['POST'])
@login_required
def add_asset():
    item_id = int(request.form.get('item_id', 0))
    asset_no = request.form.get('asset_no', '').strip()
    model = request.form.get('model', '').strip()
    serial = request.form.get('serial', '').strip()
    note = request.form.get('note', '').strip()

    if not item_id or not asset_no:
        flash('物品和资产编号不能为空', 'error')
        return redirect(url_for('items'))

    # 处理图片上传
    image = None
    image_file = request.files.get('asset_image')
    if image_file and image_file.filename:
        image = save_uploaded_file(image_file, 'assets')

    db = get_db()
    db.execute(
        'INSERT INTO assets (item_id, asset_no, image, model, serial, note, status) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (item_id, asset_no, image, model, serial, note, 'available')
    )

    # 更新物品的 total_count
    count = db.execute('SELECT COUNT(*) as c FROM assets WHERE item_id=?', (item_id,)).fetchone()['c']
    db.execute('UPDATE items SET total_count=? WHERE id=?', (count, item_id))

    db.commit()
    flash('单件资产添加成功', 'success')
    return redirect(url_for('items'))


@app.route('/asset/<int:aid>/edit', methods=['POST'])
@login_required
def edit_asset(aid):
    asset_no = request.form.get('asset_no', '').strip()
    model = request.form.get('model', '').strip()
    serial = request.form.get('serial', '').strip()
    note = request.form.get('note', '').strip()
    delete_image = request.form.get('delete_image', '') == '1'

    db = get_db()
    current = db.execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()
    if not current:
        flash('资产不存在', 'error')
        return redirect(url_for('items'))

    image = current['image']

    # 处理删除图片
    if delete_image and image:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image)
        if os.path.exists(image_path):
            os.remove(image_path)
        image = None

    # 处理新图片上传
    image_file = request.files.get('asset_image')
    if image_file and image_file.filename:
        new_image = save_uploaded_file(image_file, 'assets')
        if new_image:
            if image:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], image)
                if os.path.exists(old_path):
                    os.remove(old_path)
            image = new_image

    db.execute(
        'UPDATE assets SET asset_no=?, image=?, model=?, serial=?, note=? WHERE id=?',
        (asset_no, image, model, serial, note, aid)
    )
    db.commit()
    flash('资产信息已更新', 'success')
    return redirect(url_for('items'))


@app.route('/asset/<int:aid>/delete', methods=['POST'])
@login_required
def delete_asset(aid):
    db = get_db()
    asset = db.execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()
    if not asset:
        flash('资产不存在', 'error')
        return redirect(url_for('items'))

    if asset['status'] == 'borrowed':
        flash('该资产正在借用中，无法删除', 'error')
        return redirect(url_for('items'))

    # 删除图片
    if asset['image']:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], asset['image'])
        if os.path.exists(image_path):
            os.remove(image_path)

    item_id = asset['item_id']
    _audit('delete', 'asset', aid, f'asset_no={asset["asset_no"]}')
    db.execute('DELETE FROM assets WHERE id=?', (aid,))

    # 更新物品的 total_count
    count = db.execute('SELECT COUNT(*) as c FROM assets WHERE item_id=?', (item_id,)).fetchone()['c']
    db.execute('UPDATE items SET total_count=? WHERE id=?', (count, item_id))

    db.commit()
    flash('资产已删除', 'success')
    return redirect(url_for('items'))


# ---------- 借用管理 ----------
@app.route('/borrow/add', methods=['GET', 'POST'])
@login_required
def add_borrow():
    db = get_db()
    if request.method == 'POST':
        person_id = int(request.form.get('person_id', 0))
        borrow_date = request.form.get('borrow_date', '') or datetime.now().strftime('%Y-%m-%d')
        expected_return_date = request.form.get('expected_return_date', '') or None
        note = request.form.get('note', '').strip()

        if not person_id:
            flash('请选择借用人员', 'error')
            return redirect(url_for('add_borrow'))

        # 获取选中的单件资产列表
        asset_ids = request.form.getlist('asset_ids')

        if not asset_ids:
            flash('请至少选择一件资产', 'error')
            return redirect(url_for('add_borrow', person_id=person_id))

        # 检查每件资产状态
        errors = []
        valid_assets = []
        for aid in asset_ids:
            aid = int(aid)
            asset = db.execute(
                'SELECT a.*, i.name as item_name FROM assets a JOIN items i ON a.item_id=i.id WHERE a.id=?',
                (aid,)
            ).fetchone()
            if not asset:
                errors.append(f'资产不存在')
            elif asset['status'] != 'available':
                errors.append(f'「{asset["asset_no"]}」已被借出')
            else:
                valid_assets.append(asset)

        if errors:
            flash('；'.join(errors), 'error')
            return redirect(url_for('add_borrow', person_id=person_id))

        # 批量插入借用记录 + 更新资产状态
        for asset in valid_assets:
            db.execute('''
                INSERT INTO borrows (person_id, item_id, asset_id, quantity, borrow_date,
                                     expected_return_date, note, status)
                VALUES (?, ?, ?, 1, ?, ?, ?, 'borrowed')
            ''', (person_id, asset['item_id'], asset['id'],
                  borrow_date, expected_return_date, note))
            db.execute("UPDATE assets SET status='borrowed' WHERE id=?", (asset['id'],))

        db.commit()
        flash(f'借用登记成功！共 {len(valid_assets)} 件', 'success')
        return redirect(url_for('index'))

    persons = db.execute('SELECT * FROM persons ORDER BY name').fetchall()

    # 获取所有可用的单件资产（按物品分组）
    all_assets = db.execute('''
        SELECT a.*, i.name as item_name, i.category as item_category, i.model as item_model
        FROM assets a
        JOIN items i ON a.item_id = i.id
        WHERE a.status = 'available'
        ORDER BY i.name, a.asset_no
    ''').fetchall()

    # 按物品分组
    asset_by_item = {}
    for a in all_assets:
        iid = a['item_id']
        if iid not in asset_by_item:
            asset_by_item[iid] = {
                'item_id': iid,
                'item_name': a['item_name'],
                'item_category': a['item_category'],
                'item_model': a['item_model'],
                'assets': []
            }
        asset_by_item[iid]['assets'].append(a)

    # 转成列表并排序
    item_groups = sorted(asset_by_item.values(), key=lambda x: x['item_name'])

    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('borrow_form.html',
                           persons=persons, item_groups=item_groups, today=today,
                           pre_person=request.args.get('person_id', 0, type=int))


@app.route('/borrows/batch_return_by_person/<int:person_id>', methods=['POST'])
@login_required
def batch_return_by_person(person_id):
    """归还指定人员的所有借用中物品（事务：全成或全败）"""
    db = get_db()
    return_date = datetime.now().strftime('%Y-%m-%d')
    borrows = db.execute("SELECT * FROM borrows WHERE person_id=? AND status='borrowed'", (person_id,)).fetchall()
    if not borrows:
        return jsonify({'ok': True, 'msg': '该人员没有借用中的物品', 'success': 0})
    try:
        with db:
            for b in borrows:
                if b['asset_id']:
                    db.execute("UPDATE assets SET status='available' WHERE id=?", (b['asset_id'],))
                db.execute("UPDATE borrows SET status='returned', actual_return_date=? WHERE id=?",
                           (return_date, b['id']))
    except Exception as e:
        return jsonify({'ok': False, 'msg': '批量归还失败：' + str(e)}), 500
    return jsonify({'ok': True, 'msg': '成功归还 {} 件物品'.format(len(borrows)), 'success': len(borrows)})


@app.route('/borrows/batch_return', methods=['POST'])
@login_required
def batch_return_borrows():
    """批量归还：接收 borrow_id 列表（事务：全成或全败；已归还/不存在的先过滤掉）"""
    import json
    body = request.get_json(silent=True)
    if not body:
        body = request.form
    raw_ids = body.get('ids', [])
    if isinstance(raw_ids, str):
        raw_ids = [int(x.strip()) for x in raw_ids.split(',') if x.strip().isdigit()]
    else:
        raw_ids = [int(x) for x in raw_ids if str(x).isdigit()]

    if not raw_ids:
        return jsonify({'ok': False, 'msg': '未选择任何记录'}), 400

    db = get_db()
    return_date = datetime.now().strftime('%Y-%m-%d')

    # 先查哪些是真的 borrowed，过滤 skip
    placeholders = ','.join('?' * len(raw_ids))
    rows = db.execute(
        "SELECT id, asset_id, status FROM borrows WHERE id IN ({})".format(placeholders), raw_ids
    ).fetchall()
    valid = [r for r in rows if r['status'] == 'borrowed']
    skip = len(raw_ids) - len(valid)

    try:
        with db:
            for r in valid:
                if r['asset_id']:
                    db.execute("UPDATE assets SET status='available' WHERE id=?", (r['asset_id'],))
                db.execute("UPDATE borrows SET status='returned', actual_return_date=? WHERE id=?",
                           (return_date, r['id']))
    except Exception as e:
        return jsonify({'ok': False, 'msg': '批量归还失败：' + str(e)}), 500

    msg = '批量归还完成：成功 {} 条'.format(len(valid))
    if skip:
        msg += '，跳过 {} 条（已归还或不存在）'.format(skip)
    return jsonify({'ok': True, 'msg': msg, 'success': len(valid), 'skip': skip, 'fail': 0})


@app.route('/borrow/<int:bid>/return', methods=['POST'])
@login_required
def return_borrow(bid):
    db = get_db()
    return_date = request.form.get('return_date', '') or datetime.now().strftime('%Y-%m-%d')

    # 获取借用记录，找到关联的资产
    borrow = db.execute('SELECT * FROM borrows WHERE id=?', (bid,)).fetchone()
    if borrow and borrow['asset_id']:
        # 更新资产状态为可用
        db.execute("UPDATE assets SET status='available' WHERE id=?", (borrow['asset_id'],))

    db.execute(
        "UPDATE borrows SET status='returned', actual_return_date=? WHERE id=?",
        (return_date, bid)
    )
    db.commit()
    flash('物品已归还', 'success')
    return redirect(request.referrer or url_for('index'))


@app.route('/borrow/<int:bid>/edit', methods=['POST'])
@login_required
def edit_borrow(bid):
    """编辑借用记录（备注、预计归还日期）"""
    db = get_db()
    note = request.form.get('note', '').strip()
    expected_return_date = request.form.get('expected_return_date', '') or None

    db.execute(
        'UPDATE borrows SET note=?, expected_return_date=? WHERE id=?',
        (note, expected_return_date, bid)
    )
    db.commit()
    flash('记录已更新', 'success')
    return redirect(request.referrer or url_for('records'))


@app.route('/borrow/<int:bid>/delete', methods=['POST'])
@login_required
def delete_borrow(bid):
    db = get_db()
    b = db.execute('SELECT person_id, asset_id, status FROM borrows WHERE id=?', (bid,)).fetchone()
    db.execute('DELETE FROM borrows WHERE id=?', (bid,))
    _audit('delete', 'borrow', bid, f'person={b["person_id"] if b else ""} asset={b["asset_id"] if b else ""}')
    db.commit()
    flash('记录已删除', 'success')
    return redirect(request.referrer or url_for('index'))


@app.route('/records')
@login_required
def records():
    """所有借用记录（分页 + 逾期 tab + 搜索）"""
    db = get_db()
    status = request.args.get('status', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = int(request.args.get('per_page', 30))
    search = request.args.get('q', '').strip()

    today = datetime.now().strftime('%Y-%m-%d')

    # 先算总数（分页用）——和 data_sql 同样的 JOIN 结构
    base_join = ''' FROM borrows b
        JOIN persons p ON b.person_id = p.id
        JOIN items i ON b.item_id = i.id
        LEFT JOIN assets a ON b.asset_id = a.id
        WHERE 1=1 '''
    count_sql = 'SELECT COUNT(*) ' + base_join
    data_sql = '''
        SELECT b.*, p.name as person_name, p.avatar as person_avatar,
               i.name as item_name, i.image as item_image, i.category as item_category,
               a.asset_no as asset_no, a.image as asset_image,
               a.model as model, a.serial as serial
    ''' + base_join
    params = []
    if status == 'borrowed':
        cond = "b.status = 'borrowed'"
    elif status == 'returned':
        cond = "b.status = 'returned'"
    elif status == 'overdue':
        cond = "b.status = 'borrowed' AND b.expected_return_date IS NOT NULL AND b.expected_return_date < ?"
        params.append(today)
    else:
        cond = None

    if cond:
        count_sql += ' AND ' + cond
        data_sql += ' AND ' + cond

    # 搜索：人名 / 物品名 / 资产编号 / 备注（全字段模糊匹配）
    if search:
        like = f'%{search}%'
        count_sql += ' AND (p.name LIKE ? OR i.name LIKE ? OR a.asset_no LIKE ? OR b.note LIKE ?)'
        data_sql += ' AND (p.name LIKE ? OR i.name LIKE ? OR a.asset_no LIKE ? OR b.note LIKE ?)'
        params.extend([like, like, like, like])

    total = db.execute(count_sql, params).fetchone()[0]

    data_sql += ' ORDER BY b.borrow_date DESC, b.id DESC LIMIT ? OFFSET ?'
    page = max(1, page)
    offset = (page - 1) * per_page
    rows = db.execute(data_sql, params + [per_page, offset]).fetchall()

    # 标注逾期（前端模板用）
    overdue_ids = set()
    for r in rows:
        if r['status'] == 'borrowed' and r['expected_return_date'] and r['expected_return_date'] < today:
            overdue_ids.add(r['id'])

    total_pages = max(1, (total + per_page - 1) // per_page)

    # 各 tab 计数（导航显示用）
    counts = {
        'all': total,
        'borrowed': db.execute("SELECT COUNT(*) FROM borrows WHERE status='borrowed'").fetchone()[0],
        'returned': db.execute("SELECT COUNT(*) FROM borrows WHERE status='returned'").fetchone()[0],
        'overdue': db.execute(
            "SELECT COUNT(*) FROM borrows WHERE status='borrowed' AND expected_return_date IS NOT NULL AND expected_return_date < ?",
            (today,)).fetchone()[0],
    }

    return render_template('records.html', borrows=rows, status=status,
                           overdue_ids=overdue_ids, search=search,
                           page=page, per_page=per_page, total=total,
                           total_pages=total_pages, counts=counts)


# ---------- API ----------
@app.route('/api/person/<int:pid>/borrows')
@login_required
def api_person_borrows(pid):
    db = get_db()
    borrows = db.execute('''
        SELECT b.*, i.name as item_name, i.category as item_category,
               i.model as item_model, i.image as item_image
        FROM borrows b
        JOIN items i ON b.item_id = i.id
        WHERE b.person_id = ? AND b.status = 'borrowed'
        ORDER BY b.borrow_date DESC
    ''', (pid,)).fetchall()
    return jsonify([dict(b) for b in borrows])


@app.route('/api/items/available')
@login_required
def api_items_available():
    db = get_db()
    items = db.execute('SELECT * FROM items ORDER BY name').fetchall()
    result = []
    for item in items:
        asset_stats = db.execute(
            '''SELECT COUNT(*) as total,
                      SUM(CASE WHEN status='borrowed' THEN 1 ELSE 0 END) as borrowed,
                      SUM(CASE WHEN status='available' THEN 1 ELSE 0 END) as available
               FROM assets WHERE item_id=?''',
            (item['id'],)
        ).fetchone()
        result.append({
            'id': item['id'],
            'name': item['name'],
            'category': item['category'],
            'model': item['model'],
            'total': asset_stats['total'] or 0,
            'borrowed': asset_stats['borrowed'] or 0,
            'available': asset_stats['available'] or 0
        })
    return jsonify(result)


# ---------- 导入导出 ----------
@app.route('/export/persons')
@login_required
def export_persons():
    """导出人员列表为 Excel"""
    db = get_db()
    persons = db.execute('SELECT * FROM persons ORDER BY name').fetchall()

    if not HAS_OPENPYXL:
        flash('请先安装 openpyxl 以支持 Excel 导出', 'error')
        return redirect(url_for('persons'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '人员列表'

    # 表头
    headers = ['姓名', '部门', '电话', '备注', '创建时间']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)
        ws.cell(row=1, column=col).font = openpyxl.styles.Font(bold=True)

    # 数据
    for row, p in enumerate(persons, 2):
        ws.cell(row=row, column=1, value=p['name'])
        ws.cell(row=row, column=2, value=p['department'] or '')
        ws.cell(row=row, column=3, value=p['phone'] or '')
        ws.cell(row=row, column=4, value=p['note'] or '')
        ws.cell(row=row, column=5, value=p['created_at'] or '')

    # 调整列宽
    for col in range(1, 6):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 20

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f'人员列表_{datetime.now().strftime("%Y%m%d")}.xlsx'
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/export/items')
@login_required
def export_items():
    """导出物品列表为 Excel"""
    db = get_db()
    items = db.execute('SELECT * FROM items ORDER BY name').fetchall()

    if not HAS_OPENPYXL:
        flash('请先安装 openpyxl 以支持 Excel 导出', 'error')
        return redirect(url_for('items'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '物品列表'

    # 表头
    headers = ['物品名称', '分类', '型号/规格', '序列号', '总数', '备注', '创建时间']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)
        ws.cell(row=1, column=col).font = openpyxl.styles.Font(bold=True)

    # 数据
    for row, item in enumerate(items, 2):
        ws.cell(row=row, column=1, value=item['name'])
        ws.cell(row=row, column=2, value=item['category'] or '')
        ws.cell(row=row, column=3, value=item['model'] or '')
        ws.cell(row=row, column=4, value=item['serial'] or '')
        ws.cell(row=row, column=5, value=item['total_count'])
        ws.cell(row=row, column=6, value=item['note'] or '')
        ws.cell(row=row, column=7, value=item['created_at'] or '')

    for col in range(1, 8):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 20

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f'物品列表_{datetime.now().strftime("%Y%m%d")}.xlsx'
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/export/records')
@login_required
def export_records():
    """导出借用记录为 Excel"""
    db = get_db()
    borrows = db.execute('''
        SELECT b.*, p.name as person_name, i.name as item_name, i.category as item_category,
               a.asset_no as asset_no, a.model as model, a.serial as serial
        FROM borrows b
        JOIN persons p ON b.person_id = p.id
        JOIN items i ON b.item_id = i.id
        LEFT JOIN assets a ON b.asset_id = a.id
        ORDER BY b.borrow_date DESC
    ''').fetchall()

    if not HAS_OPENPYXL:
        flash('请先安装 openpyxl 以支持 Excel 导出', 'error')
        return redirect(url_for('records'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '借用记录'

    # 表头
    headers = ['借用人员', '物品名称', '资产编号', '型号', '序列号', '物品分类', '借用日期', '预计归还', '实际归还', '状态', '备注']
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)
        ws.cell(row=1, column=col).font = openpyxl.styles.Font(bold=True)

    # 数据
    status_map = {'borrowed': '借用中', 'returned': '已归还'}
    for row, b in enumerate(borrows, 2):
        ws.cell(row=row, column=1, value=b['person_name'])
        ws.cell(row=row, column=2, value=b['item_name'])
        ws.cell(row=row, column=3, value=b['asset_no'] or '')
        ws.cell(row=row, column=4, value=b['model'] or '')
        ws.cell(row=row, column=5, value=b['serial'] or '')
        ws.cell(row=row, column=6, value=b['item_category'] or '')
        ws.cell(row=row, column=7, value=b['borrow_date'])
        ws.cell(row=row, column=8, value=b['expected_return_date'] or '')
        ws.cell(row=row, column=9, value=b['actual_return_date'] or '')
        ws.cell(row=row, column=10, value=status_map.get(b['status'], b['status']))
        ws.cell(row=row, column=11, value=b['note'] or '')

    for col in range(1, 12):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = 18

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f'借用记录_{datetime.now().strftime("%Y%m%d")}.xlsx'
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/export/summary')
@login_required
def export_summary():
    """导出人员借用汇总（人员+物品在同一张表，按人员分组）"""
    db = get_db()

    # 获取所有人员
    persons = db.execute('SELECT * FROM persons ORDER BY name').fetchall()

    if not HAS_OPENPYXL:
        flash('请先安装 openpyxl 以支持 Excel 导出', 'error')
        return redirect(url_for('index'))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '人员借用汇总'

    # 表头样式
    header_font = openpyxl.styles.Font(bold=True, color='FFFFFFFF')
    header_fill = openpyxl.styles.PatternFill(start_color='FF667eea', end_color='FF667eea', fill_type='solid')
    person_fill = openpyxl.styles.PatternFill(start_color='FFeef2ff', end_color='FFeef2ff', fill_type='solid')
    person_font = openpyxl.styles.Font(bold=True, size=12)
    center_align = openpyxl.styles.Alignment(horizontal='center', vertical='center')
    left_align = openpyxl.styles.Alignment(horizontal='left', vertical='center')

    # 表头
    headers = ['序号', '人员姓名', '部门', '电话', '物品名称', '资产编号', '型号', '序列号', '物品分类', '借用日期', '预计归还', '状态', '备注']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align

    # 数据 - 按人员分组
    row_idx = 2
    seq = 1
    for person in persons:
        # 获取该人员的借用记录
        borrows = db.execute('''
            SELECT b.*, i.name as item_name, i.category as item_category, i.model as item_model,
                   a.asset_no as asset_no, a.model as model, a.serial as serial
            FROM borrows b
            JOIN items i ON b.item_id = i.id
            LEFT JOIN assets a ON b.asset_id = a.id
            WHERE b.person_id = ? AND b.status = 'borrowed'
            ORDER BY b.borrow_date DESC
        ''', (person['id'],)).fetchall()

        if len(borrows) == 0:
            # 没有借用物品的人员，也显示一行
            ws.cell(row=row_idx, column=1, value=seq).alignment = center_align
            ws.cell(row=row_idx, column=2, value=person['name']).font = person_font
            ws.cell(row=row_idx, column=3, value=person['department'] or '')
            ws.cell(row=row_idx, column=4, value=person['phone'] or '')
            ws.cell(row=row_idx, column=5, value='- 无借用物品 -')
            ws.cell(row=row_idx, column=5).font = openpyxl.styles.Font(color='FF9ca3af', italic=True)
            for col in range(1, 14):
                ws.cell(row=row_idx, column=col).fill = person_fill
            row_idx += 1
            seq += 1
        else:
            # 有借用物品的人员，每条记录一行
            for i, b in enumerate(borrows):
                ws.cell(row=row_idx, column=1, value=seq if i == 0 else '').alignment = center_align
                ws.cell(row=row_idx, column=2, value=person['name'] if i == 0 else '')
                if i == 0:
                    ws.cell(row=row_idx, column=2).font = person_font
                ws.cell(row=row_idx, column=3, value=person['department'] or '' if i == 0 else '')
                ws.cell(row=row_idx, column=4, value=person['phone'] or '' if i == 0 else '')
                ws.cell(row=row_idx, column=5, value=b['item_name'])
                ws.cell(row=row_idx, column=6, value=b['asset_no'] or '')
                ws.cell(row=row_idx, column=7, value=b['model'] or '')
                ws.cell(row=row_idx, column=8, value=b['serial'] or '')
                ws.cell(row=row_idx, column=9, value=b['item_category'] or '')
                ws.cell(row=row_idx, column=10, value=b['borrow_date'])
                ws.cell(row=row_idx, column=11, value=b['expected_return_date'] or '')
                ws.cell(row=row_idx, column=12, value='借用中')
                ws.cell(row=row_idx, column=12).font = openpyxl.styles.Font(color='FFd97706', bold=True)
                ws.cell(row=row_idx, column=13, value=b['note'] or '')

                # 第一条记录用人员底色
                if i == 0:
                    for col in range(1, 14):
                        ws.cell(row=row_idx, column=col).fill = person_fill

                row_idx += 1
            seq += 1

    # 调整列宽
    col_widths = [6, 12, 14, 14, 18, 14, 14, 16, 12, 14, 14, 10, 20]
    for col, width in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

    # 冻结首行
    ws.freeze_panes = 'A2'

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f'人员借用汇总_{datetime.now().strftime("%Y%m%d")}.xlsx'
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/import/persons', methods=['POST'])
@login_required
def import_persons():
    """导入人员列表"""
    if not HAS_OPENPYXL:
        flash('请先安装 openpyxl 以支持 Excel 导入', 'error')
        return redirect(url_for('persons'))

    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要导入的文件', 'error')
        return redirect(url_for('persons'))

    try:
        wb = openpyxl.load_workbook(file)
        ws = wb.active

        db = get_db()
        success_count = 0
        skip_count = 0

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            name = str(row[0]).strip() if row[0] else ''
            if not name:
                skip_count += 1
                continue

            department = str(row[1]).strip() if len(row) > 1 and row[1] else ''
            phone = str(row[2]).strip() if len(row) > 2 and row[2] else ''
            note = str(row[3]).strip() if len(row) > 3 and row[3] else ''

            try:
                db.execute(
                    'INSERT INTO persons (name, department, phone, note) VALUES (?, ?, ?, ?)',
                    (name, department, phone, note)
                )
                success_count += 1
            except sqlite3.IntegrityError:
                skip_count += 1

        db.commit()
        flash(f'导入成功！新增 {success_count} 人，跳过 {skip_count} 条', 'success')
    except Exception as e:
        flash(f'导入失败：{str(e)}', 'error')

    return redirect(url_for('persons'))


@app.route('/import/items', methods=['POST'])
@login_required
def import_items():
    """导入物品列表"""
    if not HAS_OPENPYXL:
        flash('请先安装 openpyxl 以支持 Excel 导入', 'error')
        return redirect(url_for('items'))

    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要导入的文件', 'error')
        return redirect(url_for('items'))

    try:
        wb = openpyxl.load_workbook(file)
        ws = wb.active

        db = get_db()
        success_count = 0
        skip_count = 0

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            name = str(row[0]).strip() if row[0] else ''
            if not name:
                skip_count += 1
                continue

            category = str(row[1]).strip() if len(row) > 1 and row[1] else ''
            model = str(row[2]).strip() if len(row) > 2 and row[2] else ''
            serial = str(row[3]).strip() if len(row) > 3 and row[3] else ''
            total_count = 1
            if len(row) > 4 and row[4]:
                try:
                    total_count = int(row[4])
                except (ValueError, TypeError):
                    total_count = 1
            note = str(row[5]).strip() if len(row) > 5 and row[5] else ''

            db.execute(
                'INSERT INTO items (name, category, model, serial, total_count, note) VALUES (?, ?, ?, ?, ?, ?)',
                (name, category, model, serial, total_count, note)
            )
            success_count += 1

        db.commit()
        flash(f'导入成功！新增 {success_count} 种物品，跳过 {skip_count} 条', 'success')
    except Exception as e:
        flash(f'导入失败：{str(e)}', 'error')

    return redirect(url_for('items'))


@app.route('/import/summary', methods=['POST'])
@login_required
def import_summary():
    """导入人员借用汇总表（人员+物品+资产+借用记录级联写入）"""
    if not HAS_OPENPYXL:
        flash('请先安装 openpyxl 以支持 Excel 导入', 'error')
        return redirect(url_for('index'))

    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要导入的文件', 'error')
        return redirect(url_for('index'))

    try:
        wb = openpyxl.load_workbook(file)
        ws = wb.active

        # 表头驱动定位列（避免固定列序号）
        header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        header_map = {}
        aliases = {
            '人员姓名': ['人员姓名', '姓名', 'person'],
            '部门': ['部门', 'department'],
            '电话': ['电话', 'phone', '联系方式'],
            '物品名称': ['物品名称', '物品', 'name', 'item'],
            '资产编号': ['资产编号', 'asset_no', 'asset_no'],
            '型号': ['型号', 'model'],
            '序列号': ['序列号', 'serial'],
            '物品分类': ['物品分类', '分类', 'category'],
            '借用日期': ['借用日期', 'borrow_date'],
            '预计归还': ['预计归还', '预计归还日期', 'expected_return'],
            '状态': ['状态', 'status'],
            '备注': ['备注', 'note', 'remark'],
        }
        for col_idx, cell in enumerate(header_row):
            cell_str = str(cell).strip() if cell else ''
            for canon, alist in aliases.items():
                if cell_str in alist:
                    header_map[canon] = col_idx
                    break

        # 必填表头校验
        required = ['人员姓名', '物品名称', '资产编号']
        missing = [r for r in required if r not in header_map]
        if missing:
            flash('Excel 缺少必要列: {}（需含: 人员姓名/物品名称/资产编号）'.format(
                '、'.join(missing)), 'error')
            return redirect(url_for('index'))

        db = get_db()
        new_persons = new_items = new_assets = new_borrows = skip = 0
        errors = []

        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            # 安全取值
            def h(key):
                idx = header_map.get(key)
                if idx is None or idx >= len(row) or row[idx] is None:
                    return ''
                return str(row[idx]).strip()

            person_name = h('人员姓名')
            item_name = h('物品名称')
            asset_no = h('资产编号')
            if not person_name or not item_name or not asset_no:
                skip += 1
                continue

            dept = h('部门')
            phone = h('电话')
            model = h('型号')
            serial = h('序列号')
            category = h('物品分类')
            borrow_date = h('借用日期')
            expected_return = h('预计归还')
            status_str = h('状态')
            note = h('备注')

            try:
                # 1) 人员：按姓名匹配，不存在则新建
                cur = db.execute('SELECT id FROM persons WHERE name=?', (person_name,))
                person = cur.fetchone()
                if person:
                    person_id = person['id']
                    # 顺带补全 department/phone（新值覆盖空值）
                    if dept or phone:
                        db.execute('UPDATE persons SET department=COALESCE(NULLIF(department,""),?), phone=COALESCE(NULLIF(phone,""),?) WHERE id=?',
                                   (dept or None, phone or None, person_id))
                else:
                    cur = db.execute(
                        'INSERT INTO persons (name, department, phone, note) VALUES (?, ?, ?, ?)',
                        (person_name, dept or None, phone or None, note or None))
                    person_id = cur.lastrowid
                    new_persons += 1

                # 2) 物品：按名称匹配，不存在则新建（若提供了分类/型号则合并）
                cur = db.execute('SELECT id FROM items WHERE name=?', (item_name,))
                item = cur.fetchone()
                if item:
                    item_id = item['id']
                    if category or model:
                        db.execute('UPDATE items SET category=COALESCE(NULLIF(category,""),?), model=COALESCE(NULLIF(model,""),?) WHERE id=?',
                                   (category or None, model or None, item_id))
                else:
                    cur = db.execute(
                        'INSERT INTO items (name, category, model, serial, total_count, note) VALUES (?, ?, ?, ?, 1, ?)',
                        (item_name, category or None, model or None, serial or None, note or None))
                    item_id = cur.lastrowid
                    new_items += 1

                # 3) 资产：按 asset_no + item_id 匹配，不存在则新建
                cur = db.execute('SELECT id, status FROM assets WHERE item_id=? AND asset_no=?',
                                 (item_id, asset_no))
                asset = cur.fetchone()
                if asset:
                    asset_id = asset['id']
                    # 补全型号/序列号/备注
                    if model or serial or note:
                        db.execute('UPDATE assets SET model=COALESCE(NULLIF(model,""),?), serial=COALESCE(NULLIF(serial,""),?), note=COALESCE(NULLIF(note,""),?) WHERE id=?',
                                   (model or None, serial or None, note or None, asset_id))
                else:
                    # 根据借用状态确定初始值
                    asset_status = 'available'
                    cur = db.execute(
                        'INSERT INTO assets (item_id, asset_no, model, serial, note, status) VALUES (?, ?, ?, ?, ?, ?)',
                        (item_id, asset_no, model or None, serial or None, note or None, asset_status))
                    asset_id = cur.lastrowid
                    new_assets += 1

                # 4) 借用记录：判断 Excel 中该行是否表示"借用中"
                #    状态字段为空/借用中 → 视为 active borrow；已归还 → 不写 borrows（或只写 returned）
                is_active = not status_str or status_str in ('借用中', '借出', '在借', 'borrowed', 'BORROWED')
                is_returned = status_str in ('已归还', '归还', 'returned', 'RETURNED')

                if borrow_date and is_active:
                    # 幂等检查：该 asset 有没有 active borrow
                    cur = db.execute(
                        'SELECT id FROM borrows WHERE asset_id=? AND status=?',
                        (asset_id, 'borrowed'))
                    existing = cur.fetchone()
                    if not existing:
                        cur = db.execute(
                            '''INSERT INTO borrows
                               (person_id, item_id, asset_id, quantity, borrow_date, expected_return, status, note)
                               VALUES (?, ?, ?, 1, ?, ?, 'borrowed', ?)''',
                            (person_id, item_id, asset_id, borrow_date, expected_return or None, note or None))
                        new_borrows += 1
                        # 同步更新 asset 状态
                        db.execute("UPDATE assets SET status='borrowed' WHERE id=?", (asset_id,))
                elif borrow_date and is_returned:
                    # 已归还：只写 borrows 记录，不影响 asset 当前状态（由 active borrow 控制）
                    cur = db.execute(
                        'SELECT id FROM borrows WHERE asset_id=? AND borrow_date=? AND status=?',
                        (asset_id, borrow_date, 'returned'))
                    if not cur.fetchone():
                        db.execute(
                            '''INSERT INTO borrows
                               (person_id, item_id, asset_id, quantity, borrow_date, expected_return, actual_return, status, note)
                               VALUES (?, ?, ?, 1, ?, ?, ?, 'returned', ?)''',
                            (person_id, item_id, asset_id, borrow_date, expected_return or None, borrow_date, note or None))
                        new_borrows += 1

            except Exception as ex:
                errors.append('第{}行: {}'.format(row_idx, str(ex)))
                skip += 1
                db.rollback()
                continue

        db.commit()

        # 构建结果消息
        flash_parts = ['导入完成']
        if new_persons:
            flash_parts.append('新增人员 {}'.format(new_persons))
        if new_items:
            flash_parts.append('新增物品 {}'.format(new_items))
        if new_assets:
            flash_parts.append('新增资产 {}'.format(new_assets))
        if new_borrows:
            flash_parts.append('新增借用 {}'.format(new_borrows))
        if skip:
            flash_parts.append('跳过 {}'.format(skip))
        if not any([new_persons, new_items, new_assets, new_borrows]):
            flash_parts.append('（无新数据）')
        flash('，'.join(flash_parts), 'success')
        for e in errors[:5]:
            flash(e, 'error')
        if len(errors) > 5:
            flash('...另有 {} 行错误'.format(len(errors) - 5), 'error')

    except Exception as e:
        flash('导入失败：{}'.format(str(e)), 'error')

    return redirect(url_for('index'))


# ---------- 启动 ----------
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
