from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify, make_response
from captcha.image import ImageCaptcha
import random
import string
import sqlite3
import qrcode
import os
import re
import datetime
import json
from werkzeug.security import generate_password_hash, check_password_hash
import requests
# Giả sử bạn có thư viện pyzbar hoặc tương tự để decode barcode từ ảnh
# from pyzbar.pyzbar import decode # Ví dụ, bạn cần cài đặt: pip install pyzbar
from PIL import Image  # Pillow đã có sẵn nếu bạn dùng ImageCaptcha
import io


# Placeholder cho hàm decode, bạn cần thay thế bằng thư viện thực tế
def decode(image):
    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
        if image.mode != 'L':
            image = image.convert('L')
        print(f"Decoding image. Mode: {image.mode}, Size: {image.size}")
        decoded_objects = pyzbar_decode(image)
        if decoded_objects:
            return decoded_objects
        return []
    except Exception as e:
        print(f"Lỗi khi decode mã vạch: {str(e)}")
        return []


def generate_order_code():
    now = datetime.datetime.now()
    return f"ORD-{now.strftime('%Y%m%d-%H%M%S')}-{random.randint(100, 999)}"


def generate_internal_product_id():
    return str(random.randint(100000, 999999))


app = Flask(__name__)


@app.route('/api/process_barcode_image', methods=['POST'])
def process_barcode_image():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Không có file ảnh được gửi'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Không có file nào được chọn'}), 400

    try:
        # Xử lý ảnh và decode mã vạch
        image = Image.open(file.stream)
        decoded_objects = decode(image)

        if not decoded_objects:
            return jsonify({'success': False, 'message': 'Không tìm thấy mã vạch trong ảnh'}), 400

        barcode = decoded_objects[0].data.decode('utf-8')
        app.logger.info(f"Đã giải mã được mã vạch: {barcode}")

        # Gọi API Open Food Facts
        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            result = response.json()

            if result.get('status') == 1 and result.get('product'):
                product_data = result.get('product', {})
                product_info = {
                    'name': product_data.get('product_name_vi', product_data.get('product_name', 'Không rõ tên')),
                    'manufacturer': product_data.get('brands', 'Không rõ hãng'),
                    'origin': product_data.get('countries_tags', ['Không rõ xuất xứ'])[0].replace('en:', '').replace(
                        '-', ' ').title(),
                    'volume': product_data.get('quantity', 'Không rõ dung tích/khối lượng'),
                    'image_url': product_data.get('image_front_url', product_data.get('image_url', None)),
                    'barcode': barcode,
                    'ingredients': product_data.get('ingredients_text_vi',
                                                    product_data.get('ingredients_text', 'Không có thông tin')),
                    'nutrition_data': {
                        'energy': product_data.get('nutriments', {}).get('energy-kcal_100g', 'N/A'),
                        'proteins': product_data.get('nutriments', {}).get('proteins_100g', 'N/A'),
                        'carbohydrates': product_data.get('nutriments', {}).get('carbohydrates_100g', 'N/A'),
                        'fat': product_data.get('nutriments', {}).get('fat_100g', 'N/A')
                    },
                    'categories': product_data.get('categories_tags', []),
                    'packaging': product_data.get('packaging', 'Không có thông tin'),
                    'serving_size': product_data.get('serving_size', 'Không có thông tin'),
                    'stores': product_data.get('stores', 'Không có thông tin'),
                    'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                return jsonify({'success': True, 'product': product_info})
            else:
                return jsonify({'success': False, 'message': 'Không tìm thấy sản phẩm trên Open Food Facts'})

        except requests.exceptions.RequestException as e:
            app.logger.error(f"API: OpenFoodFacts - Request error: {e}")
            return jsonify({'success': False, 'message': f'Lỗi khi gọi API Open Food Facts: {e}'}), 500

    except Exception as e:
        app.logger.error(f"Error processing barcode image: {e}")
        return jsonify({'success': False, 'message': f'Lỗi khi xử lý ảnh: {str(e)}'}), 500


@app.route('/api/update-inventory', methods=['POST'])
def update_inventory():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'Không có dữ liệu được gửi'}), 400

    try:
        conn = get_db_connection()
        c = conn.cursor()

        # Tạo mã QR chứa thông tin sản phẩm
        qr_data = {
            'name': data['name'],
            'manufacturer': data['manufacturer'],
            'origin': data['origin'],
            'volume': data['volume'],
            'nutrition_data': data['nutrition_data'],
            'scan_date': data['scan_date']
        }

        # Tạo thư mục lưu mã QR nếu chưa tồn tại
        qr_folder = os.path.join('static', 'qrcodes')
        if not os.path.exists(qr_folder):
            os.makedirs(qr_folder)

        # Tạo tên file QR duy nhất
        qr_filename = f"qr_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        qr_path = os.path.join(qr_folder, qr_filename)

        # Tạo mã QR
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(json.dumps(qr_data))
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img.save(qr_path)

        # Thêm sản phẩm vào bảng products
        c.execute("""
            INSERT INTO products (
                name, manufacturer, origin, volume_weight, date_added,
                qty, product_qr_code_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            data['name'],
            data['manufacturer'],
            data['origin'],
            data['volume'],
            data['scan_date'],
            data['quantity'],
            os.path.join('qrcodes', qr_filename)
        ))

        # Thêm log hoạt động
        c.execute("""
            INSERT INTO activity_log (
                user_email, action_type, product_id, quantity, action_date
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            session['username'],
            'nhap_kho',
            c.lastrowid,  # ID của sản phẩm vừa thêm
            data['quantity'],
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))

        conn.commit()

        return jsonify({
            'success': True,
            'message': 'Cập nhật kho thành công',
            'qr_code_url': url_for('static', filename=os.path.join('qrcodes', qr_filename))
        })

    except Exception as e:
        app.logger.error(f"Lỗi khi cập nhật kho: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return jsonify({'success': False, 'message': f'Lỗi khi cập nhật kho: {str(e)}'}), 500
    finally:
        if 'conn' in locals():
            conn.close()


@app.route('/api/scan', methods=['POST'])
def scan_product_openfoodfacts():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    data = request.get_json()
    barcode = data.get('code')

    if not barcode:
        return jsonify({'success': False, 'message': 'Không nhận được mã vạch'}), 400

    app.logger.info(f"API: OpenFoodFacts - Searching for barcode: {barcode}")
    url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        result = response.json()

        if result.get('status') == 1 and result.get('product'):
            product_data = result.get('product', {})
            product_info = {
                'name': product_data.get('product_name_vi', product_data.get('product_name', 'Không rõ tên')),
                'manufacturer': product_data.get('brands', 'Không rõ hãng'),
                'origin': product_data.get('countries_tags', ['Không rõ xuất xứ'])[0].replace('en:', '').replace('-',
                                                                                                                 ' ').title(),
                'volume': product_data.get('quantity', 'Không rõ dung tích/khối lượng'),
                'image_url': product_data.get('image_front_url', product_data.get('image_url', None)),
                'barcode': barcode,
                'ingredients': product_data.get('ingredients_text_vi',
                                                product_data.get('ingredients_text', 'Không có thông tin')),
                'nutrition_data': {
                    'energy': product_data.get('nutriments', {}).get('energy-kcal_100g', 'N/A'),
                    'proteins': product_data.get('nutriments', {}).get('proteins_100g', 'N/A'),
                    'carbohydrates': product_data.get('nutriments', {}).get('carbohydrates_100g', 'N/A'),
                    'fat': product_data.get('nutriments', {}).get('fat_100g', 'N/A')
                },
                'categories': product_data.get('categories_tags', []),
                'packaging': product_data.get('packaging', 'Không có thông tin'),
                'serving_size': product_data.get('serving_size', 'Không có thông tin'),
                'stores': product_data.get('stores', 'Không có thông tin'),
                'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            # Lưu thông tin vào CSDL
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("""
                    INSERT INTO products (
                        name, barcode_data, product_id_internal, manufacturer, origin,
                        volume_weight, date_added, product_qr_code_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(barcode_data) DO UPDATE SET
                    name=excluded.name,
                    manufacturer=excluded.manufacturer,
                    origin=excluded.origin,
                    volume_weight=excluded.volume_weight,
                    date_added=excluded.date_added
                """, (
                    product_info['name'],
                    barcode,
                    generate_internal_product_id(),
                    product_info['manufacturer'],
                    product_info['origin'],
                    product_info['volume'],
                    product_info['scan_date'],
                    None  # QR code path sẽ được cập nhật sau
                ))

                product_id = c.lastrowid

                # Tạo QR code cho sản phẩm
                qr_data = {
                    'product_id': product_id,
                    'name': product_info['name'],
                    'barcode': barcode,
                    'manufacturer': product_info['manufacturer'],
                    'origin': product_info['origin'],
                    'volume': product_info['volume']
                }

                qr_folder_path = os.path.join(os.path.dirname(__file__), 'static', 'product_qrcodes')
                if not os.path.exists(qr_folder_path):
                    os.makedirs(qr_folder_path)

                qr_filename = f"product_{product_id}.png"
                qr_file_path = os.path.join(qr_folder_path, qr_filename)

                # Tạo QR code
                qr = qrcode.QRCode(version=1, box_size=10, border=5)
                qr.add_data(json.dumps(qr_data))
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white")
                qr_img.save(qr_file_path)

                # Cập nhật đường dẫn QR trong CSDL
                qr_path_for_db = os.path.join('product_qrcodes', qr_filename).replace("\\", "/")
                c.execute("UPDATE products SET product_qr_code_path = ? WHERE id = ?",
                          (qr_path_for_db, product_id))

                conn.commit()

                # Thêm đường dẫn QR vào thông tin trả về
                product_info['qr_code_path'] = qr_path_for_db

            except Exception as db_error:
                app.logger.error(f"Database error: {db_error}")
                if 'conn' in locals():
                    conn.rollback()
                    conn.close()
                return jsonify({'success': False, 'message': f'Lỗi khi lưu vào CSDL: {str(db_error)}'}), 500
            finally:
                if 'conn' in locals():
                    conn.close()

            app.logger.info(f"API: OpenFoodFacts - Product found and saved: {product_info.get('name')}")
            return jsonify({'success': True, 'product': product_info})
        else:
            app.logger.warning(f"API: OpenFoodFacts - Product not found or status not 1 for barcode: {barcode}")
            return jsonify({'success': False, 'message': 'Không tìm thấy sản phẩm trên Open Food Facts'})

    except requests.exceptions.RequestException as e:
        app.logger.error(f"API: OpenFoodFacts - Request error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi khi gọi API Open Food Facts: {e}'}), 500
    except Exception as e:
        app.logger.error(f"API: OpenFoodFacts - Unexpected error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi không xác định: {e}'}), 500


@app.after_request
def add_security_headers(response):
    response.headers['Permissions-Policy'] = 'camera=(self)'
    return response


@app.route("/scan_page_test")
def scan_page():
    if 'username' not in session:
        session['username'] = 'test_user_email@example.com'  # Giả sử email cho user test
    response = make_response(render_template("mobile_scan_screen.html"))
    return response


app.secret_key = 'your_very_secret_and_complex_key_here_CHANGE_ME'  # Đảm bảo bạn thay đổi key này

# Kết nối CSDL
DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'sales.db')


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # Giúp truy cập cột bằng tên
    return conn


# Khởi tạo bảng (chạy một lần hoặc kiểm tra tồn tại)
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Bảng users: username sẽ lưu email, email cũng lưu email. Cả hai đều UNIQUE.
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        phone TEXT,
        password TEXT,
        is_verified BOOLEAN DEFAULT 0
    )
    """)
    # Các bảng khác giữ nguyên
    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL DEFAULT 0,
        qty INTEGER DEFAULT 0,
        category TEXT,
        product_id_internal TEXT UNIQUE,
        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expiry_date DATE,
        product_qr_code_path TEXT,
        barcode_data TEXT,
        volume_weight TEXT,
        manufacturer TEXT,
        origin TEXT,
        energy REAL,
        proteins REAL,
        carbohydrates REAL,
        fat REAL,
        ingredients TEXT,
        serving_size TEXT,
        packaging TEXT,
        stores TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_code TEXT UNIQUE NOT NULL,
        customer_name TEXT,
        customer_phone TEXT,
        customer_address TEXT,
        order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'Mới',
        total_amount REAL DEFAULT 0,
        notes TEXT,
        qr_code_path TEXT
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        product_id INTEGER,
        product_name TEXT,
        quantity INTEGER,
        price_at_order REAL,
        FOREIGN KEY (order_id) REFERENCES orders (id),
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT NOT NULL,
        action_type TEXT NOT NULL,
        product_id INTEGER,
        product_name TEXT,
        quantity INTEGER,
        action_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        details TEXT,
        barcode_data TEXT,
        nutrition_data TEXT,
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    """)
    conn.commit()
    conn.close()


init_db()  # Gọi hàm khởi tạo DB


@app.route('/captcha')
def captcha():
    image = ImageCaptcha(width=180, height=70)
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    session['captcha_code'] = code
    data = image.generate(code)
    return send_file(io.BytesIO(data.getvalue()), mimetype='image/png')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error = None
    email_value = ''  # Sẽ lưu email người dùng nhập để điền lại vào form nếu lỗi

    if 'username' in session:  # 'username' trong session giờ lưu email
        return redirect(url_for('product_dashboard_overview'))

    if request.method == 'POST':
        email_input = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        email_value = email_input  # Giữ lại email đã nhập

        if not email_input or not password:
            error = 'Vui lòng nhập email và mật khẩu.'
        else:
            conn = get_db_connection()
            c = conn.cursor()
            # Tìm người dùng bằng email
            c.execute("SELECT id, username, password, is_verified, email FROM users WHERE email=?", (email_input,))
            user_data = c.fetchone()
            conn.close()

            if user_data and check_password_hash(user_data['password'], password):
                if user_data['is_verified'] == 1:  # Hoặc True nếu bạn lưu là boolean
                    session['username'] = user_data['email']  # Lưu email vào session['username']
                    session['user_id'] = user_data['id']  # Lưu user_id nếu cần
                    flash('Đăng nhập thành công!', 'success')
                    return redirect(url_for('product_dashboard_overview'))
                else:
                    error = 'Tài khoản của bạn chưa được xác thực.'
            else:
                error = 'Email hoặc mật khẩu không đúng.'
    return render_template('login.html', error=error, email_value=email_value)


def generate_otp(length=6):
    return "".join(random.choices(string.digits, k=length))


@app.route('/register', methods=['GET', 'POST'])
def register():
    error = ''
    # Giữ lại giá trị form để điền lại nếu có lỗi, bỏ username vì không dùng trực tiếp từ form này
    form_data = {'email': '', 'phone': ''}
    if request.method == 'POST':
        # Lấy dữ liệu từ form
        email_from_form = request.form.get('email', '').strip()
        phone_from_form = request.form.get('phone', '').strip()
        password_from_form = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        captcha_input = request.form.get('captcha', '')
        captcha_session = session.get('captcha_code', '')
        first_name = request.form.get('firstName', '').strip()
        last_name = request.form.get('lastName', '').strip()

        # Cập nhật form_data để điền lại nếu lỗi
        form_data['email'] = email_from_form
        form_data['phone'] = phone_from_form
        form_data['firstName'] = first_name
        form_data['lastName'] = last_name

        # Validate dữ liệu
        if not email_from_form or not password_from_form or not confirm_password or not captcha_input or not first_name or not last_name:
            error = 'Vui lòng điền đầy đủ thông tin.'
        elif password_from_form != confirm_password:
            error = 'Mật khẩu không khớp.'
        elif len(password_from_form) < 8:
            error = 'Mật khẩu phải có ít nhất 8 ký tự.'
        elif not re.search(r'[A-Z]', password_from_form):
            error = 'Mật khẩu phải chứa ít nhất một chữ cái viết hoa.'
        elif not re.search(r'[!@#$%^&*(),.?":{}|<>]', password_from_form):
            error = 'Mật khẩu phải chứa ít nhất một ký tự đặc biệt.'
        elif captcha_input.upper() != captcha_session.upper():
            error = 'Mã captcha không đúng.'
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email_from_form):
            error = 'Địa chỉ email không hợp lệ.'

        if not error:
            conn = get_db_connection()
            c = conn.cursor()
            # Kiểm tra xem email đã tồn tại chưa
            c.execute("SELECT id FROM users WHERE email=?", (email_from_form,))
            existing_user = c.fetchone()

            if existing_user:
                error = 'Email này đã được đăng ký.'
                conn.close()
            else:
                try:
                    hashed_password = generate_password_hash(password_from_form)
                    # username trong DB sẽ lưu email
                    username_for_db = email_from_form

                    try:
                        c.execute(
                            "INSERT INTO users (username, email, phone, password, is_verified) VALUES (?, ?, ?, ?, ?)",
                            (username_for_db, email_from_form, phone_from_form, hashed_password, 1)
                            # is_verified = 1 (tự động xác thực)
                        )
                        conn.commit()
                        user_id = c.lastrowid  # Lấy id của user vừa tạo
                        conn.close()

                        session['username'] = email_from_form  # Lưu email vào session
                        session['user_id'] = user_id

                        flash('Đăng ký thành công! Bạn đã được đăng nhập.', 'success')
                        return redirect(url_for('product_dashboard_overview'))
                    except Exception as e:
                        error = f'Lỗi khi đăng ký: {str(e)}'
                        if 'conn' in locals():
                            conn.rollback()
                            conn.close()

                except sqlite3.IntegrityError:  # Có thể xảy ra nếu có ràng buộc UNIQUE khác bị vi phạm (dù đã check email)
                    error = 'Đã có lỗi xảy ra với cơ sở dữ liệu (ví dụ: email đã tồn tại - kiểm tra lại). Vui lòng thử lại.'
                    conn.rollback()  # Rollback nếu có lỗi
                    conn.close()
                except Exception as e:
                    error = f'Lỗi không xác định trong quá trình đăng ký: {str(e)}'
                    app.logger.error(f"Registration error: {e}")
                    conn.rollback()
                    conn.close()
        # Nếu có lỗi ở trên, sẽ không vào đây, nhưng nếu có lỗi từ DB thì cần đóng kết nối
        # if 'conn' in locals() and conn: # Đảm bảo conn tồn tại trước khi đóng
        #     conn.close()

    # Tạo captcha mới cho mỗi lần tải lại trang hoặc sau khi submit
    new_captcha_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    session['captcha_code'] = new_captcha_code

    return render_template('register.html', error=error, form_data=form_data)


@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('user_id', None)
    flash("Bạn đã đăng xuất.", "info")
    return redirect(url_for('index'))


@app.route('/qr-generator-tool', methods=['GET', 'POST'])
def qr_generator_tool():
    if 'username' not in session: return redirect(url_for('login_page'))
    qr_image_path_display = None
    data_generated = None
    if request.method == 'POST':
        data = request.form['data']
        if data:
            img = qrcode.make(data)
            static_dir = os.path.join(os.path.dirname(__file__), 'static')
            if not os.path.exists(static_dir): os.makedirs(static_dir)
            img_path_on_disk = os.path.join(static_dir, 'qr_tool_generated.png')
            img.save(img_path_on_disk)
            qr_image_path_display = 'qr_tool_generated.png'
            data_generated = data
            flash("Mã QR đã được tạo.", "success")
        else:
            flash("Vui lòng nhập dữ liệu để tạo mã QR.", "warning")
    return render_template('qr_tool.html', qr_image_path=qr_image_path_display, data_generated=data_generated)


# ... (Các route khác giữ nguyên, đảm bảo kiểm tra 'username' in session nếu cần bảo vệ) ...
# Ví dụ:
@app.route('/quan-ly-san-pham/')
@app.route('/quan-ly-san-pham/tong-quan')
def product_dashboard_overview():
    if 'username' not in session:  # 'username' trong session là email
        return redirect(url_for('login_page'))
    # Lấy thông tin người dùng từ session nếu cần hiển thị
    current_user_email = session.get('username')
    return render_template(
        'product_dashboard_content_tongquan.html',
        mobile_nav_type='main_dashboard_nav',
        current_user_email=current_user_email
    )


CATEGORY_DETAILS = {
    "thuc-pham": {
        "display_name": "Hàng Thực phẩm",
        "image_url": "https://images.pexels.com/photos/264636/pexels-photo-264636.jpeg?auto=compress&cs=tinysrgb&w=800",
        "description": "Quản lý các mặt hàng thực phẩm, đồ uống và các sản phẩm tiêu dùng hàng ngày"
    },
    "gia-dung": {
        "display_name": "Đồ gia dụng",
        "image_url": "https://images.pexels.com/photos/6585751/pexels-photo-6585751.jpeg?auto=compress&cs=tinysrgb&w=800",
        "description": "Quản lý các thiết bị, dụng cụ gia đình và đồ dùng sinh hoạt"
    },
    "thoi-trang": {
        "display_name": "Thời trang",
        "image_url": "https://images.pexels.com/photos/996329/pexels-photo-996329.jpeg?auto=compress&cs=tinysrgb&w=800",
        "description": "Quản lý các sản phẩm quần áo, phụ kiện thời trang và làm đẹp"
    },
    "may-tinh-linh-kien": {
        "display_name": "Máy tính & Linh kiện",
        "image_url": "https://images.pexels.com/photos/2582937/pexels-photo-2582937.jpeg?auto=compress&cs=tinysrgb&w=800",
        "description": "Quản lý máy tính, laptop, linh kiện điện tử và thiết bị công nghệ"
    }
}


@app.route('/quan-ly-san-pham/danh-muc/<string:category_slug>')
def manage_category_placeholder(category_slug):
    if 'username' not in session:
        return redirect(url_for('login_page'))

    category_info = CATEGORY_DETAILS.get(category_slug)

    if not category_info:
        flash("Danh mục không tồn tại.", "danger")
        return redirect(url_for('product_dashboard_overview'))

    return render_template(
        'category_management_page.html',  # Bạn cần tạo template này
        category_slug=category_slug,
        category_display_name=category_info["display_name"],
        category_bg_image=category_info["image_url"],
        category_description=category_info["description"],
        page_specific_title=f'{category_info["display_name"]}',
        mobile_nav_type='category_detail_nav'
    )


# Routes cho Quản lý QR
@app.route('/quan-ly-qr')
def qr_management_overview():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    return render_template(
        'product_dashboard_content_tongquan.html',
        mobile_nav_type='main_dashboard_nav',
        current_user_email=session.get('username')
    )


@app.route('/quan-ly-qr/danh-muc/<string:category_slug>')
def qr_management_detail(category_slug):
    if 'username' not in session:
        return redirect(url_for('login_page'))

    category_info = CATEGORY_DETAILS.get(category_slug)
    if not category_info:
        flash("Danh mục không tồn tại.", "danger")
        return redirect(url_for('product_dashboard_overview'))

    return render_template(
        'qr_management_detail.html',
        category_slug=category_slug,
        category_display_name=category_info["display_name"],
        category_bg_image=category_info["image_url"],
        category_description=category_info["description"],
        page_specific_title=f'Quản lý {category_info["display_name"]}',
        mobile_nav_type='qr_management_nav'
    )


@app.route('/quan-ly-qr/tong-hop')
def qr_management_summary():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    return render_template(
        'qr_management_summary.html',
        mobile_nav_type='qr_management_nav'
    )


@app.route('/quan-ly-san-pham/danh-muc-chi-tiet/<string:category_slug>')
def category_detail_with_nav(category_slug):
    if 'username' not in session:
        return redirect(url_for('login_page'))
    category_info = CATEGORY_DETAILS.get(category_slug)
    if not category_info:
        flash("Danh mục không tồn tại.", "danger")
        return redirect(url_for('product_dashboard_overview'))
    return render_template(
        'category_detail_with_nav.html',  # Bạn cần tạo template này
        category_slug=category_slug,
        category_display_name=category_info["display_name"],
        # ... các thông tin khác ...
        mobile_nav_type='category_detail_nav'
    )


@app.route('/quan-ly-san-pham/tong-quan/chi-tiet')
def product_dashboard_overview_detail():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(id) FROM products")
        total_products = c.fetchone()[0] or 0
        c.execute("SELECT SUM(qty) FROM products")
        total_items_in_stock = c.fetchone()[0] or 0
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        c.execute("SELECT COUNT(id) FROM orders WHERE DATE(order_date) = ?", (today_str,))
        orders_today = c.fetchone()[0] or 0
        LOW_STOCK_THRESHOLD = 5
        c.execute("SELECT name, qty FROM products WHERE qty > 0 AND qty <= ? ORDER BY qty ASC LIMIT 5",
                  (LOW_STOCK_THRESHOLD,))
        low_stock_products = c.fetchall()
        EXPIRY_ALERT_DAYS = 30
        c.execute(f"""
            SELECT name, expiry_date FROM products
            WHERE expiry_date IS NOT NULL AND DATE(expiry_date) BETWEEN DATE('now') AND DATE('now', '+{EXPIRY_ALERT_DAYS} days')
            ORDER BY expiry_date ASC
        """)
        expiring_products = c.fetchall()
        conn.close()
        overview_data = {
            'total_products': total_products,
            'total_items_in_stock': total_items_in_stock,
            'orders_today': orders_today,
            'low_stock_products': low_stock_products,
            'expiring_products': expiring_products
        }
        return render_template('product_dashboard_content_tongquan_chitiet.html', overview_data=overview_data)
    except Exception as e:
        app.logger.error(f"Error loading overview detail: {e}")
        flash("Có lỗi xảy ra khi tải trang chi tiết.", "danger")
        return redirect(url_for('product_dashboard_overview'))


@app.route('/quan-ly-san-pham/ton-kho')
def pd_ton_kho_quan_ly():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, name, price, qty, category, product_id_internal, date_added, expiry_date, product_qr_code_path
        FROM products ORDER BY date_added DESC, name
    """)
    products_list = [dict(row) for row in c.fetchall()]  # Chuyển đổi sang list of dicts
    conn.close()
    return render_template(
        'product_dashboard_content_tonkho.html',
        products=products_list,
        mobile_nav_type='main_dashboard_nav'
    )


@app.route('/quan-ly-san-pham/bao-cao')
def pd_bao_cao_xem():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    return render_template(
        'product_dashboard_content_baocao.html',
        mobile_nav_type='main_dashboard_nav'
    )


@app.route('/quan-ly-san-pham/thong-tin-ca-nhan')
def pd_user_profile():
    if 'username' not in session: return redirect(url_for('login_page'))
    user_email = session.get('username')
    # Lấy thêm thông tin user từ DB nếu cần (ví dụ: phone)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT email, phone FROM users WHERE email = ?", (user_email,))
    user_info_db = c.fetchone()
    conn.close()
    user_info_display = {'email': user_email, 'phone': user_info_db['phone'] if user_info_db else 'N/A'}

    return render_template('product_dashboard_content_user_profile.html', user_info=user_info_display)


@app.route('/quan-ly-san-pham/nhap-san-pham-moi', methods=['GET', 'POST'])
def pd_nhap_san_pham_moi():
    if 'username' not in session: return redirect(url_for('login_page'))
    if request.method == 'POST':
        # Xử lý logic thêm sản phẩm mới
        # ...
        product_name = request.form.get('product_name')
        if product_name:
            # ... (logic tạo QR, lưu DB)
            flash(f"Sản phẩm '{product_name}' đã được thêm (logic mẫu).", "success")
            return redirect(url_for('pd_ton_kho_quan_ly'))
        else:
            flash("Tên sản phẩm không được để trống.", "warning")

    return render_template('product_dashboard_content_nhap_sanpham.html')


@app.route('/quan-ly-san-pham/nhap-kho-qua-quet')
def pd_nhap_kho_quet_page():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    context_slug_from_url = request.args.get('context_slug')
    nav_type_to_use = 'category_context_nav' if context_slug_from_url else 'main_dashboard_nav'
    return render_template(
        'product_dashboard_content_scan_and_input.html',
        contextual_sidebar='category_management',  # Cần xem xét lại biến này
        mobile_nav_type=nav_type_to_use,
        category_slug=context_slug_from_url
    )


@app.route('/quan-ly-san-pham/xuat-kho-qua-quet')
def pd_xuat_kho_quet_page():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    return render_template('product_dashboard_content_xuatkho_scan.html')


@app.route('/api/get-product-info-from-scan', methods=['POST'])
def get_product_info_from_scan():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập hoặc phiên hết hạn'}), 401

    data = request.get_json()
    scanned_data = data.get('scanned_data')

    if not scanned_data:
        return jsonify({'error': 'Không nhận được dữ liệu mã quét'}), 400

    app.logger.info(f"API: Yêu cầu thông tin sản phẩm cho mã quét: {scanned_data}")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, name, product_id_internal, barcode_data, price, qty, category, expiry_date
        FROM products
        WHERE product_id_internal = ? OR barcode_data = ?
    """, (scanned_data, scanned_data))
    product_row = c.fetchone()
    conn.close()

    if product_row:
        product_details = dict(product_row)  # Chuyển sqlite3.Row thành dict
        if product_details.get('expiry_date'):  # Định dạng lại ngày nếu có
            product_details['expiry_date'] = datetime.datetime.strptime(product_details['expiry_date'],
                                                                        '%Y-%m-%d %H:%M:%S').strftime(
                '%Y-%m-%d') if isinstance(product_details['expiry_date'], str) else product_details[
                'expiry_date'].strftime('%Y-%m-%d')

        app.logger.info(f"API: Sản phẩm được tìm thấy: {product_details}")
        return jsonify(product_details), 200
    else:
        app.logger.warning(f"API: Không tìm thấy sản phẩm cho mã quét: {scanned_data}")
        return jsonify({'error': 'Sản phẩm không tồn tại trong hệ thống'}), 404


@app.route('/api/update-inventory-and-log', methods=['POST'])
def update_inventory_and_log():
    if 'username' not in session:
        return jsonify({'error': 'Chưa đăng nhập'}), 401
    if 'user_id' not in session:  # Cần user_id để log
        return jsonify({'error': 'Thông tin user_id không có trong session'}), 401

    try:
        data = request.get_json()
        app.logger.info(f"API: Update inventory data: {data}")

        scanned_data = data.get('scanned_data')  # Đây có thể là barcode hoặc product_id_internal
        name_from_api = data.get('product_name')
        manufacturer_from_api = data.get('manufacturer')
        origin_from_api = data.get('origin')
        volume_from_api = data.get('volume')
        action = data.get('action')  # 'nhap' hoặc 'xuat'
        quantity = int(data.get('quantity', 0))

        if not scanned_data or not action or quantity <= 0:
            return jsonify({'error': 'Dữ liệu không hợp lệ (thiếu mã quét, hành động hoặc số lượng <=0)'}), 400

        user_id_from_session = session['user_id']
        log_message = ""
        product_id_for_log = None
        product_name_for_log = name_from_api  # Mặc định là tên từ API (nếu là sản phẩm mới)

        conn = get_db_connection()
        c = conn.cursor()

        if action == 'nhap':
            # Kiểm tra xem sản phẩm (dựa trên barcode) đã có trong DB chưa
            c.execute(
                "SELECT id, name, qty, product_id_internal FROM products WHERE barcode_data = ?",
                (scanned_data,))
            product_in_db = c.fetchone()

            if product_in_db:  # Sản phẩm đã tồn tại, cập nhật số lượng
                db_product_id = product_in_db['id']
                db_product_name = product_in_db['name']
                new_qty = product_in_db['qty'] + quantity
                c.execute("UPDATE products SET qty = ? WHERE id = ?", (new_qty, db_product_id))
                log_message = f"Đã nhập thêm {quantity} cho sản phẩm '{db_product_name}'. Tồn kho mới: {new_qty}."
                product_id_for_log = db_product_id
                product_name_for_log = db_product_name
                app.logger.info(
                    f"Stocked in {quantity} for existing product ID {db_product_id} with barcode {scanned_data}")
            else:  # Sản phẩm mới, thêm vào DB
                internal_id = generate_internal_product_id()
                # Tạo QR code cho product_id_internal (nếu cần)
                qr_data_internal = url_for('view_product_details_by_qr', product_internal_id=internal_id,
                                           _external=True)
                qr_folder_path = os.path.join(os.path.dirname(__file__), 'static', 'product_qrcodes')
                if not os.path.exists(qr_folder_path): os.makedirs(qr_folder_path)
                qr_filename = f"product_{internal_id}.png"
                qr_file_path_on_disk = os.path.join(qr_folder_path, qr_filename)
                product_qr_code_path_for_db = None
                try:
                    qr_img = qrcode.make(qr_data_internal)
                    qr_img.save(qr_file_path_on_disk)
                    product_qr_code_path_for_db = os.path.join('product_qrcodes', qr_filename).replace("\\",
                                                                                                       "/")  # Đảm bảo dùng /
                except Exception as e_qr:
                    app.logger.error(f"Error generating QR for new product: {e_qr}")

                c.execute("""
                    INSERT INTO products (name, barcode_data, product_id_internal, qty, product_qr_code_path,
                                          manufacturer, origin, volume_weight, category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name_from_api or f"SP_{scanned_data}", scanned_data, internal_id, quantity,
                      product_qr_code_path_for_db, manufacturer_from_api, origin_from_api, volume_from_api,
                      data.get('category_slug')))  # Thêm category nếu có

                db_product_id = c.lastrowid
                product_id_for_log = db_product_id
                product_name_for_log = name_from_api or f"SP_{scanned_data}"
                log_message = f"Đã tạo sản phẩm mới '{product_name_for_log}' và nhập {quantity} đơn vị."
                app.logger.info(
                    f"Created new product '{product_name_for_log}' ID {db_product_id} with barcode {scanned_data}, qty {quantity}")

        elif action == 'xuat':
            # Khi xuất, scanned_data có thể là product_id_internal (từ QR của hệ thống) hoặc barcode_data (từ barcode sản phẩm)
            c.execute("SELECT id, name, qty FROM products WHERE product_id_internal = ? OR barcode_data = ?",
                      (scanned_data, scanned_data))
            product_in_db = c.fetchone()

            if not product_in_db:
                conn.close()
                return jsonify({'error': 'Sản phẩm không tồn tại trong kho để xuất.'}), 404

            db_product_id = product_in_db['id']
            db_product_name = product_in_db['name']
            current_qty = product_in_db['qty']
            product_id_for_log = db_product_id
            product_name_for_log = db_product_name

            if current_qty < quantity:
                conn.close()
                return jsonify({'error': f"Không đủ số lượng '{db_product_name}' để xuất. Tồn kho: {current_qty}"}), 400

            c.execute("UPDATE products SET qty = qty - ? WHERE id = ?", (quantity, db_product_id))
            log_message = f"Đã xuất {quantity} sản phẩm '{db_product_name}'."
            app.logger.info(f"Stocked out {quantity} for product ID {db_product_id} with identifier {scanned_data}")

        else:
            conn.close()
            return jsonify({'error': 'Hành động không hợp lệ'}), 400

        # Ghi log sau khi hành động nhập/xuất thành công
        c.execute("""
            INSERT INTO scan_log (scanned_data, product_id, product_name_at_scan, user_id, action_type, quantity_changed)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (scanned_data, product_id_for_log, product_name_for_log, user_id_from_session, action,
              quantity if action == 'nhap' else -quantity))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': log_message, 'product_id': product_id_for_log,
                        'product_name': product_name_for_log}), 200

    except Exception as e:
        app.logger.error(f"API: Error updating inventory and log: {e}")
        if 'conn' in locals() and conn: conn.rollback(); conn.close()
        return jsonify({'error': f'Lỗi server: {str(e)}'}), 500


@app.route('/mobile-scan-product')
def render_mobile_scan_page():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    category_slug = request.args.get('category_slug')  # Lấy category_slug từ query param
    return render_template('mobile_scan_screen.html', category_slug=category_slug)


@app.route('/san-pham/qr-info/<product_internal_id>')
def view_product_details_by_qr(product_internal_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE product_id_internal = ?", (product_internal_id,))
    product_data = c.fetchone()
    conn.close()
    if product_data:
        return render_template('view_product_by_qr.html', product=dict(product_data))
    else:
        flash("Không tìm thấy thông tin sản phẩm cho mã QR này.", "warning")
        return redirect(url_for('product_dashboard_overview'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
