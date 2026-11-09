// ===== 模态框操作 =====
function openModal(modalId) {
    document.getElementById(modalId).classList.add('show');
    document.body.style.overflow = 'hidden';
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('show');
    document.body.style.overflow = '';
    // 若转移人员下拉浮窗还开着，一并收起
    if (window.__pselectOpen && typeof togglePersonPanel === 'function') togglePersonPanel();
}

// 点击遮罩关闭模态框
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('show');
        document.body.style.overflow = '';
        if (window.__pselectOpen && typeof togglePersonPanel === 'function') togglePersonPanel();
    }
});

// ESC 关闭模态框（人员下拉浮窗打开时先收起浮窗，不清模态框）
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        if (window.__pselectOpen && typeof togglePersonPanel === 'function') return;
        document.querySelectorAll('.modal-overlay.show').forEach(function(el) {
            el.classList.remove('show');
        });
        document.body.style.overflow = '';
    }
});

// ===== 人员卡片展开 =====
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.person-card-header').forEach(function(header) {
        header.addEventListener('click', function() {
            var card = this.closest('.person-card');
            card.classList.toggle('expanded');
        });
    });
});

// ===== 自动隐藏提示消息 =====
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(function() {
        document.querySelectorAll('.flash').forEach(function(flash) {
            flash.style.transition = 'opacity 0.5s';
            flash.style.opacity = '0';
            setTimeout(function() {
                flash.remove();
            }, 500);
        });
    }, 3000);
});

// ===== 确认删除 =====
function confirmDelete(form, message) {
    if (confirm(message || '确定要删除吗？')) {
        form.submit();
    }
    return false;
}
