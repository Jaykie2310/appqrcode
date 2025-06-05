from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify, make_response
from flask_cors import CORS
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
from PIL import Image
import io


def preprocess_image_for_barcode(image):
    """Tiền xử lý ảnh để cải thiện khả năng đọc mã vạch"""
    try:
        import cv2
        import numpy as np
        
        # Chuyển PIL Image sang OpenCV format
        opencv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        
        # Chuyển sang grayscale
        gray = cv2.cvtColor(opencv_image, cv2.COLOR_BGR2GRAY)
        
        # Áp dụng Gaussian blur để giảm noise
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Tăng độ tương phản
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(blurred)
        
        # Thử nhiều threshold khác nhau
        processed_images = []
        
        # Binary threshold
        _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        processed_images.append(Image.fromarray(binary))
        
        # Adaptive threshold
        adaptive = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        processed_images.append(Image.fromarray(adaptive))
        
        # Original enhanced
        processed_images.append(Image.fromarray(enhanced))
        
        return processed_images
    except ImportError:
        # Fallback nếu không có OpenCV
        if image.mode != 'L':
            gray = image.convert('L')
        else:
            gray = image
        return [gray]
    except Exception as e:
        app.logger.error(f"Lỗi khi tiền xử lý ảnh: {str(e)}")
        return [image.convert('L') if image.mode != 'L' else image]


def decode_with_multiple_methods(image):
    """Thử nhiều phương pháp decode khác nhau"""
    results = []
    
    # Phương pháp 1: pyzbar
    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
        decoded_objects = pyzbar_decode(image)
        if decoded_objects:
            for obj in decoded_objects:
                results.append({
                    'data': obj.data.decode('utf-8'),
                    'type': obj.type,
                    'method': 'pyzbar'
                })
    except Exception as e:
        app.logger.warning(f"pyzbar decode failed: {str(e)}")
    
    # Phương pháp 2: zxing (nếu có)
    try:
        import zxing
        reader = zxing.BarCodeReader()
        # Lưu ảnh tạm thời
        temp_path = '/tmp/temp_barcode.png'
        image.save(temp_path)
        barcode = reader.decode(temp_path)
        if barcode:
            results.append({
                'data': barcode.parsed,
                'type': barcode.format,
                'method': 'zxing'
            })
        # Xóa file tạm
        import os
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception as e:
        app.logger.warning(f"zxing decode failed: {str(e)}")
    
    # Phương pháp 3: opencv barcode detector (nếu có)
    try:
        import cv2
        import numpy as np
        
        # Chuyển đổi sang OpenCV format
        opencv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(opencv_image, cv2.COLOR_BGR2GRAY)
        
        # Sử dụng OpenCV barcode detector
        detector = cv2.barcode.BarcodeDetector()
        retval, decoded_info, decoded_type, points = detector.detectAndDecode(gray)
        
        if retval:
            for i, info in enumerate(decoded_info):
                if info:
                    results.append({
                        'data': info,
                        'type': decoded_type[i] if i < len(decoded_type) else 'unknown',
                        'method': 'opencv'
                    })
    except Exception as e:
        app.logger.warning(f"opencv decode failed: {str(e)}")
    
    return results


def decode(image):
    """Hàm decode chính với nhiều phương pháp dự phòng"""
    try:
        # Tiền xử lý ảnh
        processed_images = preprocess_image_for_barcode(image)
        
        all_results = []
        
        # Thử decode với từng ảnh đã xử lý
        for processed_img in processed_images:
            results = decode_with_multiple_methods(processed_img)
            all_results.extend(results)
        
        # Nếu có kết quả, trả về kết quả đầu tiên
        if all_results:
            app.logger.info(f"Barcode detected using {all_results[0]['method']}: {all_results[0]['data']}")
            # Chuyển đổi về format tương thích với code cũ
            class BarcodeResult:
                def __init__(self, data, type_name):
                    self.data = data.encode('utf-8')
                    self.type = type_name
            
            return [BarcodeResult(all_results[0]['data'], all_results[0]['type'])]
        
        return []
        
    except Exception as e:
        app.logger.error(f"Lỗi khi decode mã vạch: {str(e)}")
        return []


def generate_order_code():
    now = datetime.datetime.now()
    return f"ORD-{now.strftime('%Y%m%d-%H%M%S')}-{random.randint(100, 999)}"


def generate_internal_product_id():
    return str(random.randint(100000, 999999))


app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "https://appqrcodemanage.onrender.com",
            "capacitor://localhost",
            "http://localhost",
            "http://localhost:8100"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# Combined security and CORS headers
@app.after_request
def add_security_headers(response):
    # Add CORS headers
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    
    # Add camera and security headers for mobile scanning
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*, geolocation=()'
    response.headers['Feature-Policy'] = 'camera *; microphone *'
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:; media-src 'self' blob: data:;"
    
    return response


# Rest of your routes and functions remain the same...
# (I'll include the key routes for barcode scanning)

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
        image = Image.open(file.stream)
        decoded_objects = decode(image)

        if not decoded_objects:
            return jsonify({'success': False, 'message': 'Không tìm thấy mã vạch trong ảnh'}), 400

        barcode = decoded_objects[0].data.decode('utf-8')
        app.logger.info(f"Đã giải mã được mã vạch: {barcode}")

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
                # Fallback: tạo sản phẩm với thông tin cơ bản từ barcode
                app.logger.warning(f"Sản phẩm không tìm thấy trên OpenFoodFacts, tạo fallback cho barcode: {barcode}")
                fallback_product = {
                    'name': f'Sản phẩm {barcode}',
                    'manufacturer': 'Chưa xác định',
                    'origin': 'Chưa xác định',
                    'volume': 'Chưa xác định',
                    'image_url': None,
                    'barcode': barcode,
                    'ingredients': 'Chưa có thông tin',
                    'nutrition_data': {
                        'energy': 'N/A',
                        'proteins': 'N/A',
                        'carbohydrates': 'N/A',
                        'fat': 'N/A'
                    },
                    'categories': [],
                    'packaging': 'Chưa xác định',
                    'serving_size': 'Chưa xác định',
                    'stores': 'Chưa xác định',
                    'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                return jsonify({'success': True, 'product': fallback_product, 'fallback': True})

        except requests.exceptions.ConnectionError as e:
            app.logger.error(f"API: OpenFoodFacts - Connection error: {e}")
            # Fallback khi không thể kết nối
            fallback_product = {
                'name': f'Sản phẩm {barcode}',
                'manufacturer': 'Chưa xác định',
                'origin': 'Chưa xác định', 
                'volume': 'Chưa xác định',
                'image_url': None,
                'barcode': barcode,
                'ingredients': 'Chưa có thông tin',
                'nutrition_data': {
                    'energy': 'N/A',
                    'proteins': 'N/A',
                    'carbohydrates': 'N/A',
                    'fat': 'N/A'
                },
                'categories': [],
                'packaging': 'Chưa xác định',
                'serving_size': 'Chưa xác định',
                'stores': 'Chưa xác định',
                'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            return jsonify({
                'success': True, 
                'product': fallback_product, 
                'fallback': True,
                'message': 'Không thể kết nối đến cơ sở dữ liệu sản phẩm. Vui lòng nhập thông tin thủ công.'
            })
        except requests.exceptions.RequestException as e:
            app.logger.error(f"API: OpenFoodFacts - Request error: {e}")
            # Fallback cho các lỗi khác
            fallback_product = {
                'name': f'Sản phẩm {barcode}',
                'manufacturer': 'Chưa xác định',
                'origin': 'Chưa xác định',
                'volume': 'Chưa xác định',
                'image_url': None,
                'barcode': barcode,
                'ingredients': 'Chưa có thông tin',
                'nutrition_data': {
                    'energy': 'N/A',
                    'proteins': 'N/A',
                    'carbohydrates': 'N/A',
                    'fat': 'N/A'
                },
                'categories': [],
                'packaging': 'Chưa xác định',
                'serving_size': 'Chưa xác định',
                'stores': 'Chưa xác định',
                'scan_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            return jsonify({
                'success': True, 
                'product': fallback_product, 
                'fallback': True,
                'message': 'Lỗi kết nối cơ sở dữ liệu sản phẩm. Vui lòng nhập thông tin thủ công.'
            })

    except Exception as e:
        app.logger.error(f"Error processing barcode image: {e}")
        return jsonify({'success': False, 'message': f'Lỗi khi xử lý ảnh: {str(e)}'}), 500


@app.route("/scan_page_test")
def scan_page():
    if 'username' not in session:
        session['username'] = 'test_user_email@example.com'
    response = make_response(render_template("mobile_scan_screen.html"))
    # Add additional camera headers for this specific route
    response.headers['Permissions-Policy'] = 'camera=*, microphone=*'
    response.headers['Feature-Policy'] = 'camera *; microphone *'
    return response


# Add other essential routes and database functions
app.secret_key = 'your_very_secret_and_complex_key_here_CHANGE_ME'
DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'sales.db')


def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    c = conn.cursor()
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
    conn.commit()
    conn.close()


init_db()


if __name__ == '__main__':
    qr_folder = os.path.join(os.path.dirname(__file__), 'static', 'product_qrcodes')
    if not os.path.exists(qr_folder):
        os.makedirs(qr_folder)
    legacy_qr_folder = os.path.join(os.path.dirname(__file__), 'static', 'qrcodes')
    if not os.path.exists(legacy_qr_folder):
        os.makedirs(legacy_qr_folder)
    app.run(debug=True, host='0.0.0.0', port=5000)
