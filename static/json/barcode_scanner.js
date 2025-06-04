document.addEventListener("DOMContentLoaded", function() {
    const videoContainer = document.getElementById("video-container");
    const scanResultDisplay = document.getElementById("scan-result");
    const startScanButton = document.getElementById("start-scan");
    const stopScanButton = document.getElementById("stop-scan");

    // Cấu hình QuaggaJS
    const quaggaConfig = {
        inputStream: {
            name: "Live",
            type: "LiveStream",
            target: videoContainer, // Thẻ chứa video feed
            constraints: {
                facingMode: "environment" // Ưu tiên camera sau nếu có
            },
        },
        decoder: {
            readers: ["ean_reader", "ean_8_reader", "code_128_reader"] // Các loại barcode được hỗ trợ
        },
        locate: true
    };

    startScanButton.addEventListener("click", function() {
        Quagga.init(quaggaConfig, function(err) {
            if (err) {
                console.log(err);
                scanResultDisplay.innerText = "Lỗi khi khởi tạo quét mã.";
                return;
            }
            Quagga.start();
        });
    });

    stopScanButton.addEventListener("click", function() {
        Quagga.stop();
    });

    Quagga.onDetected(function(result) {
        if(result.codeResult && result.codeResult.code){
            let code = result.codeResult.code;
            scanResultDisplay.innerText = `Mã vạch được phát hiện: ${code}`;
            Quagga.stop();

            // Tùy chọn: Gọi hàm để lấy thông tin sản phẩm từ Open Food Facts dựa trên mã vạch
            // Ví dụ: callProductInfoAPI(code);
        }
    });

    // Xử lý submit upload file
    const uploadForm = document.getElementById("barcode-upload-form");
    const uploadResultDisplay = document.getElementById("upload-result");

    uploadForm.addEventListener("submit", function(e) {
        e.preventDefault();
        let formData = new FormData(uploadForm);
        fetch("/api/process_barcode_image", {
            method: "POST",
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if(data.success){
                uploadResultDisplay.innerHTML = `<pre>${JSON.stringify(data.product, null, 2)}</pre>`;
            } else {
                uploadResultDisplay.innerText = data.message;
            }
        })
        .catch(err => {
            uploadResultDisplay.innerText = "Lỗi khi tải ảnh lên.";
        });
    });
});