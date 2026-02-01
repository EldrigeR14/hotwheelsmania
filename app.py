from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
import sqlite3
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

app = Flask(__name__)
app.secret_key = 'clave_secreta'  # cámbiala por una segura

# ---------------------------
# Config reservas
# ---------------------------
HOLD_MINUTES = 120  # reserva temporal por item (minutos)

# Carpetas de subida
UPLOAD_FOLDER_IMAGES = 'uploads/images'
UPLOAD_FOLDER_LOGO   = 'uploads/logo'

for folder in (UPLOAD_FOLDER_IMAGES, UPLOAD_FOLDER_LOGO):
    os.makedirs(os.path.join('static', folder), exist_ok=True)


# ---------------------------
# Conexión DB
# ---------------------------
def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------
# Utilidades de sesión
# ---------------------------
def get_session_id() -> str:
    """
    Identificador estable de la sesión para holds (reserva temporal).
    """
    sid = session.get('sid')
    if not sid:
        sid = uuid.uuid4().hex
        session['sid'] = sid
    return sid


def get_cart_ids() -> List[int]:
    cart = session.get('cart', [])
    if not isinstance(cart, list):
        cart = []
    # normalizar a int
    out = []
    for x in cart:
        try:
            out.append(int(x))
        except:
            pass
    session['cart'] = out
    session['cart_count'] = len(out)
    return out


def set_cart_ids(cart_ids: List[int]) -> None:
    session['cart'] = cart_ids
    session['cart_count'] = len(cart_ids)


# ---------------------------
# Inicialización / Migración DB
# ---------------------------
def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Tabla products (adaptada a Hot Wheels)
    # Nota: arregla el error del script anterior: faltaba coma antes de external_link.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT UNIQUE,
            category TEXT,
            description TEXT,
            price REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'available', -- available | reserved | sold
            image_path TEXT,
            manufacturer TEXT,
            plant TEXT,
            unit TEXT,
            quantity INTEGER DEFAULT 1,
            external_link TEXT
        )
    """)

    # Migración: agregar columnas si faltan
    cur.execute("PRAGMA table_info(products)")
    cols = [r[1] for r in cur.fetchall()]

    def add_col_if_missing(col_name: str, ddl: str):
        if col_name not in cols:
            cur.execute(f"ALTER TABLE products ADD COLUMN {ddl}")

    add_col_if_missing('code', "code TEXT")
    add_col_if_missing('price', "price REAL NOT NULL DEFAULT 0")
    add_col_if_missing('status', "status TEXT NOT NULL DEFAULT 'available'")
    add_col_if_missing('category', "category TEXT")
    add_col_if_missing('description', "description TEXT")
    add_col_if_missing('image_path', "image_path TEXT")
    add_col_if_missing('external_link', "external_link TEXT")
    add_col_if_missing('quantity', "quantity INTEGER DEFAULT 1")

    # Holds: reserva temporal por sesión
    cur.execute("""
        CREATE TABLE IF NOT EXISTS holds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            product_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)

    # Orders: reserva ya procesada con datos del cliente
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            instagram TEXT NOT NULL,
            notes TEXT,
            total REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'reserved', -- reserved | contacted | paid | delivered | cancelled
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------
# Logo dinámico
# ---------------------------
def get_logo():
    logo_dir = os.path.join('static', UPLOAD_FOLDER_LOGO)
    try:
        files = os.listdir(logo_dir)
        return files[0] if files else None
    except:
        return None


# ---------------------------
# Limpieza de holds expirados
# ---------------------------
def cleanup_expired_holds():
    """
    Libera productos cuya reserva temporal expiró.
    """
    now_iso = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()

    # buscar holds expirados
    cur.execute("SELECT product_id FROM holds WHERE expires_at <= ?", (now_iso,))
    expired = [r['product_id'] for r in cur.fetchall()]

    if expired:
        # eliminar holds expirados
        cur.execute("DELETE FROM holds WHERE expires_at <= ?", (now_iso,))

        # marcar productos como available SOLO si estaban reserved
        cur.execute(
            f"UPDATE products SET status='available' "
            f"WHERE id IN ({','.join(['?']*len(expired))}) AND status='reserved'",
            expired
        )

    conn.commit()
    conn.close()


# ---------------------------
# Helpers de catálogo
# ---------------------------
def fetch_categories() -> List[str]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != '' ORDER BY category")
    cats = [r['category'] for r in cur.fetchall()]
    conn.close()
    return cats


def fetch_products(q: str = "", category: str = "") -> List[sqlite3.Row]:
    query = "SELECT * FROM products WHERE 1=1"
    params = []

    if q:
        query += " AND (name LIKE ? OR code LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY id DESC"

    conn = get_db()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_product(product_id: int) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE id=?", (product_id,))
    row = cur.fetchone()
    conn.close()
    return row


# ---------------------------
# Rutas públicas (vitrina)
# ---------------------------
@app.route('/')
def index():
    cleanup_expired_holds()

    q = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()

    products = fetch_products(q=q, category=category)
    categories = fetch_categories()

    return render_template(
        'index.html',
        products=products,
        categories=categories,
        selected_category=category if category else "",
        q=q,
        logo_file=get_logo()
    )


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    cleanup_expired_holds()

    product = fetch_product(product_id)
    if not product:
        abort(404)

    return render_template(
        'product_detail.html',
        product=product,
        logo_file=get_logo()
    )


# ---------------------------
# Carrito + Holds (reserva temporal)
# ---------------------------
@app.route('/cart')
def cart():
    cleanup_expired_holds()

    cart_ids = get_cart_ids()
    if not cart_ids:
        return render_template('cart.html', cart_items=[], total=0, logo_file=get_logo())

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM products WHERE id IN ({','.join(['?']*len(cart_ids))})",
        cart_ids
    )
    items = cur.fetchall()
    conn.close()

    # total
    total = sum(float(i['price'] or 0) for i in items)

    return render_template('cart.html', cart_items=items, total=total, logo_file=get_logo())


@app.route('/cart/add/<int:product_id>', methods=['POST'])
def add_to_cart(product_id):
    cleanup_expired_holds()

    sid = get_session_id()
    cart_ids = get_cart_ids()

    product = fetch_product(product_id)
    if not product:
        flash("Producto no encontrado.", "warning")
        return redirect(url_for('index'))

    # solo se reserva si está disponible
    if product['status'] != 'available':
        flash("Ese Hot Wheels ya no está disponible.", "warning")
        return redirect(url_for('product_detail', product_id=product_id))

    # crear hold + marcar reserved (operación “lo más atómica posible” en sqlite)
    now = datetime.utcnow()
    expires = now + timedelta(minutes=HOLD_MINUTES)

    conn = get_db()
    cur = conn.cursor()

    try:
        # insertar hold (product_id UNIQUE evita duplicados)
        cur.execute("""
            INSERT INTO holds (session_id, product_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
        """, (sid, product_id, now.isoformat(), expires.isoformat()))

        # marcar producto como reserved
        cur.execute("UPDATE products SET status='reserved' WHERE id=? AND status='available'", (product_id,))
        if cur.rowcount == 0:
            # alguien lo ganó primero
            conn.rollback()
            flash("Otro usuario reservó ese Hot Wheels antes que tú.", "warning")
            conn.close()
            return redirect(url_for('product_detail', product_id=product_id))

        conn.commit()

    except sqlite3.IntegrityError:
        # ya existe hold (otro usuario lo reservó)
        conn.rollback()
        flash("Ese Hot Wheels ya fue reservado.", "warning")
        conn.close()
        return redirect(url_for('product_detail', product_id=product_id))

    conn.close()

    # agregar al carrito de sesión
    if product_id not in cart_ids:
        cart_ids.append(product_id)
        set_cart_ids(cart_ids)

    flash(f"Reservaste temporalmente: {product['name']} (por {HOLD_MINUTES} min).", "success")
    return redirect(url_for('cart'))


@app.route('/cart/remove/<int:product_id>', methods=['POST'])
def remove_from_cart(product_id):
    cleanup_expired_holds()

    sid = get_session_id()
    cart_ids = get_cart_ids()

    if product_id in cart_ids:
        cart_ids.remove(product_id)
        set_cart_ids(cart_ids)

    # si el hold era de esta sesión, liberarlo
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT session_id FROM holds WHERE product_id=?", (product_id,))
    row = cur.fetchone()

    if row and row['session_id'] == sid:
        cur.execute("DELETE FROM holds WHERE product_id=?", (product_id,))
        cur.execute("UPDATE products SET status='available' WHERE id=? AND status='reserved'", (product_id,))
        conn.commit()

    conn.close()
    flash("Producto removido del carrito.", "info")
    return redirect(url_for('cart'))


@app.route('/cart/clear')
def clear_cart():
    cleanup_expired_holds()

    sid = get_session_id()
    cart_ids = get_cart_ids()

    if cart_ids:
        conn = get_db()
        cur = conn.cursor()

        # liberar holds propios
        cur.execute(
            f"SELECT product_id FROM holds WHERE session_id=? AND product_id IN ({','.join(['?']*len(cart_ids))})",
            [sid] + cart_ids
        )
        mine = [r['product_id'] for r in cur.fetchall()]

        if mine:
            cur.execute(
                f"DELETE FROM holds WHERE session_id=? AND product_id IN ({','.join(['?']*len(mine))})",
                [sid] + mine
            )
            cur.execute(
                f"UPDATE products SET status='available' WHERE id IN ({','.join(['?']*len(mine))}) AND status='reserved'",
                mine
            )

        conn.commit()
        conn.close()

    set_cart_ids([])
    flash("Carrito vaciado.", "info")
    return redirect(url_for('cart'))


# ---------------------------
# Checkout (procesar reserva)
# ---------------------------
@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    cleanup_expired_holds()

    sid = get_session_id()
    cart_ids = get_cart_ids()

    if not cart_ids:
        flash("Tu carrito está vacío.", "warning")
        return redirect(url_for('index'))

    # cargar items
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM products WHERE id IN ({','.join(['?']*len(cart_ids))})",
        cart_ids
    )
    items = cur.fetchall()

    # validar que siguen reservados por esta sesión
    cur.execute(
        f"SELECT product_id FROM holds WHERE session_id=? AND product_id IN ({','.join(['?']*len(cart_ids))})",
        [sid] + cart_ids
    )
    held_by_me = set([r['product_id'] for r in cur.fetchall()])

    # si falta alguno, sacarlo del carrito
    valid_items = [i for i in items if i['id'] in held_by_me]
    invalid = [i['id'] for i in items if i['id'] not in held_by_me]

    if invalid:
        cart_ids = [i for i in cart_ids if i not in invalid]
        set_cart_ids(cart_ids)
        flash("Algunos productos ya no estaban reservados y se removieron del carrito.", "warning")

    if not valid_items:
        conn.close()
        flash("No tienes productos reservados actualmente.", "warning")
        return redirect(url_for('cart'))

    total = sum(float(i['price'] or 0) for i in valid_items)

    if request.method == 'GET':
        conn.close()
        return render_template('checkout.html', cart_items=valid_items, total=total, logo_file=get_logo())

    # POST: crear orden
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    instagram = request.form.get('instagram', '').strip()
    notes = request.form.get('notes', '').strip()

    if not name or not phone or not instagram:
        conn.close()
        flash("Completa nombre, teléfono e Instagram.", "danger")
        return render_template('checkout.html', cart_items=valid_items, total=total, logo_file=get_logo())

    order_code = "HW-" + uuid.uuid4().hex[:8].upper()
    now_iso = datetime.utcnow().isoformat()

    # crear order
    cur.execute("""
        INSERT INTO orders (order_code, name, phone, instagram, notes, total, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?)
    """, (order_code, name, phone, instagram, notes, total, now_iso))
    order_id = cur.lastrowid

    # order_items + marcar productos sold + borrar holds
    for it in valid_items:
        cur.execute("""
            INSERT INTO order_items (order_id, product_id, price)
            VALUES (?, ?, ?)
        """, (order_id, it['id'], float(it['price'] or 0)))

        cur.execute("UPDATE products SET status='sold' WHERE id=?", (it['id'],))

    cur.execute(
        f"DELETE FROM holds WHERE session_id=? AND product_id IN ({','.join(['?']*len(held_by_me))})",
        [sid] + list(held_by_me)
    )

    conn.commit()
    conn.close()

    # limpiar carrito
    set_cart_ids([])

    return redirect(url_for('success', order_code=order_code))


@app.route('/success/<order_code>')
def success(order_code):
    conn = get_db()
    cur = conn.cursor()

    # 1) Traer la orden
    cur.execute("SELECT * FROM orders WHERE order_code=?", (order_code,))
    order = cur.fetchone()
    if not order:
        conn.close()
        abort(404)

    # 2) Traer los items del pedido
    cur.execute("""
        SELECT p.id, p.name, p.price
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        WHERE oi.order_id = ?
        ORDER BY oi.id ASC
    """, (order['id'],))
    items = cur.fetchall()

    conn.close()

    return render_template(
        'success.html',
        order_code=order['order_code'],
        name=order['name'],
        phone=order['phone'],
        instagram=order['instagram'],
        total=float(order['total'] or 0),
        items=items,
        logo_file=get_logo()
    )



# ---------------------------
# Login Admin
# ---------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    logo_file = get_logo()
    if request.method == 'POST':
        user = request.form.get('username', '')
        password = request.form.get('password', '')
        if user == 'eldrige.rios' and password == '@141225Eer@':
            session['admin'] = True
            flash("Sesión iniciada.", "success")
            return redirect(url_for('inventario_producto'))
        flash('Credenciales incorrectas', 'danger')
    return render_template('login.html', logo_file=logo_file)


@app.route('/logout')
def logout():
    session.pop('admin', None)
    flash("Sesión cerrada.", "info")
    return redirect(url_for('index'))


# ---------------------------
# Admin: Inventario / CRUD
# ---------------------------
@app.route('/admin')

def inventario_producto():
    if not session.get('admin'):
        return redirect(url_for('login'))

    cleanup_expired_holds()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products ORDER BY id DESC")
    productos = cur.fetchall()
    conn.close()

    return render_template('inventario_producto.html', productos=productos, logo_file=get_logo())

@app.route('/admin/orders')
def admin_orders():
    if not session.get('admin'):
        return redirect(url_for('login'))

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, order_code, name, phone, instagram, total, status, created_at
        FROM orders
        ORDER BY id DESC
    """)
    orders = cur.fetchall()
    conn.close()

    return render_template('admin_orders.html', orders=orders, logo_file=get_logo())

@app.route('/admin/orders/<order_code>/delete', methods=['POST'])
def admin_order_delete(order_code):
    if not session.get('admin'):
        return redirect(url_for('login'))

    conn = get_db()
    cur = conn.cursor()

    # Buscar la orden
    cur.execute("SELECT id FROM orders WHERE order_code=?", (order_code,))
    order = cur.fetchone()
    if not order:
        conn.close()
        flash("Reserva no encontrada.", "warning")
        return redirect(url_for('admin_orders'))

    order_id = order['id']

    # Borrar items y luego la orden
    cur.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
    cur.execute("DELETE FROM orders WHERE id=?", (order_id,))

    conn.commit()
    conn.close()

    flash("Reserva eliminada.", "success")
    return redirect(url_for('admin_orders'))


@app.route('/admin/orders/<order_code>')

def admin_order_detail(order_code):
    if not session.get('admin'):
        return redirect(url_for('login'))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM orders WHERE order_code=?", (order_code,))
    order = cur.fetchone()
    if not order:
        conn.close()
        abort(404)

    cur.execute("""
        SELECT p.id, p.name, p.code, p.category, p.image_path, oi.price
        FROM order_items oi
        JOIN products p ON p.id = oi.product_id
        WHERE oi.order_id = ?
        ORDER BY oi.id ASC
    """, (order['id'],))
    items = cur.fetchall()

    conn.close()
    return render_template('admin_order_detail.html', order=order, items=items, logo_file=get_logo())


@app.route('/admin/orders/<order_code>/status', methods=['POST'])
def admin_order_update_status(order_code):
    if not session.get('admin'):
        return redirect(url_for('login'))

    new_status = request.form.get('status', '').strip()
    allowed = {'reserved', 'contacted', 'paid', 'delivered', 'cancelled'}
    if new_status not in allowed:
        flash("Estado inválido.", "danger")
        return redirect(url_for('admin_order_detail', order_code=order_code))

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status=? WHERE order_code=?", (new_status, order_code))
    conn.commit()
    conn.close()

    flash("Estado actualizado.", "success")
    return redirect(url_for('admin_order_detail', order_code=order_code))


def inventario_producto():
    if not session.get('admin'):
        return redirect(url_for('login'))

    cleanup_expired_holds()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products ORDER BY id DESC")
    productos = cur.fetchall()
    conn.close()

    return render_template('inventario_producto.html', productos=productos, logo_file=get_logo())


@app.route('/add', methods=['GET', 'POST'])
def add_product():
    if not session.get('admin'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        price = request.form.get('price', '0').strip()
        status = request.form.get('status', 'available').strip()

        image = request.files.get('image')
        img_path = ''

        if image and image.filename:
            ext = os.path.splitext(image.filename)[1].lower()
            fn = f"{uuid.uuid4().hex}{ext}"
            img_path = os.path.join(UPLOAD_FOLDER_IMAGES, fn).replace("\\", "/")
            image.save(os.path.join('static', img_path))

        try:
            price_val = float(price)
        except:
            price_val = 0.0

        if not name:
            flash("El nombre es obligatorio.", "danger")
            return render_template('add_product.html', logo_file=get_logo())

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO products (name, code, category, description, price, status, image_path, quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (name, code, category, description, price_val, status, img_path))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("El código ya existe. Usa otro.", "danger")
            conn.close()
            return render_template('add_product.html', logo_file=get_logo())

        conn.close()
        flash("Producto agregado.", "success")
        return redirect(url_for('inventario_producto'))

    return render_template('add_product.html', logo_file=get_logo())


@app.route('/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    if not session.get('admin'):
        return redirect(url_for('login'))

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE id=?", (product_id,))
    producto = cur.fetchone()
    if not producto:
        conn.close()
        abort(404)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip()
        category = request.form.get('category', '').strip()
        description = request.form.get('description', '').strip()
        price = request.form.get('price', '0').strip()
        status = request.form.get('status', 'available').strip()

        # si hay holds, mejor no dejarlo available (regla simple)
        # (puedes quitar esto si quieres)
        cur.execute("SELECT 1 FROM holds WHERE product_id=?", (product_id,))
        has_hold = cur.fetchone() is not None
        if has_hold and status == 'available':
            status = 'reserved'

        # imagen
        img_path = producto['image_path']
        image = request.files.get('image')
        if image and image.filename:
            if img_path:
                try:
                    os.remove(os.path.join('static', img_path))
                except:
                    pass
            ext = os.path.splitext(image.filename)[1].lower()
            fn = f"{uuid.uuid4().hex}{ext}"
            img_path = os.path.join(UPLOAD_FOLDER_IMAGES, fn).replace("\\", "/")
            image.save(os.path.join('static', img_path))

        try:
            price_val = float(price)
        except:
            price_val = 0.0

        if not name:
            conn.close()
            flash("El nombre es obligatorio.", "danger")
            return redirect(url_for('edit_product', product_id=product_id))

        try:
            cur.execute("""
                UPDATE products SET
                  name=?, code=?, category=?, description=?, price=?, status=?, image_path=?
                WHERE id=?
            """, (name, code, category, description, price_val, status, img_path, product_id))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("El código ya existe. Usa otro.", "danger")
            conn.close()
            return redirect(url_for('edit_product', product_id=product_id))

        conn.close()
        flash("Producto actualizado.", "success")
        return redirect(url_for('inventario_producto'))

    conn.close()
    return render_template('edit_product.html', producto=producto, logo_file=get_logo())

@app.route('/upload_logo', methods=['POST'])
def upload_logo():
    if not session.get('admin'):
        return redirect(url_for('login'))

    logo = request.files.get('logo')
    if logo and logo.filename:
        folder = os.path.join('static', UPLOAD_FOLDER_LOGO)
        for f in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, f))
            except:
                pass

        ext = os.path.splitext(logo.filename)[1].lower()
        fn = "logo" + ext
        path = os.path.join(UPLOAD_FOLDER_LOGO, fn).replace("\\", "/")
        logo.save(os.path.join('static', path))
        flash("Logo actualizado", 'success')

    return redirect(url_for('inventario_producto'))

@app.route('/delete/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    if not session.get('admin'):
        return redirect(url_for('login'))

    conn = get_db()
    cur = conn.cursor()

    # Opcional: impedir borrar si está reservado
    cur.execute("SELECT status, image_path FROM products WHERE id=?", (product_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        abort(404)

    if row['status'] == 'reserved':
        conn.close()
        flash("No puedes eliminar un producto reservado. Libéralo o espera a que expire.", "warning")
        return redirect(url_for('inventario_producto'))

    img_path = row['image_path']

    cur.execute("DELETE FROM products WHERE id=?", (product_id,))
    conn.commit()
    conn.close()

    # borrar la imagen del disco si existe
    if img_path:
        try:
            os.remove(os.path.join('static', img_path))
        except:
            pass

    flash("Producto eliminado.", "success")
    return redirect(url_for('inventario_producto'))


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)


