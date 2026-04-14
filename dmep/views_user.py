from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Q, F
from decimal import Decimal
from django.core.paginator import Paginator
from django.http import JsonResponse
from .models import Product, Cashier, Discount, SaleItem, Sale, Customer, Category
from .utils.discounts import calculate_discounted_price
from django.db import transaction


# =======================
# CART
# =======================

def add_to_cart(request, product_id):
    cart = request.session.get("cart", {})

    pid = str(product_id)

    # ensure integer quantity only
    cart[pid] = int(cart.get(pid, 0)) + 1

    request.session["cart"] = cart
    request.session.modified = True

    return redirect("cart")

def cart_view(request):
    cart = request.session.get("cart", {})

    cart_items = []
    subtotal = 0
    discount_total = 0

    for product_id, item in cart.items():
        product = Product.objects.get(id=product_id)

        if isinstance(item, dict):
            qty = item.get("qty") or item.get("quantity") or 0
        else:
            qty = item

        qty = int(qty)

        # 🔥 GET DISCOUNTED PRICE
        final_price, discount_obj, percent = calculate_discounted_price(product)

        price = float(final_price)
        original_price = float(product.selling_price or 0)

        line_total = price * qty
        subtotal += line_total

        # calculate discount saved
        if discount_obj:
            discount_total += (original_price - price) * qty

        cart_items.append({
            "key": product_id,
            "name": product.name,
            "price": price,
            "qty": qty,
            "image": product.img.url if product.img else "",
        })

    return render(request, "user/cart.html", {
        "cart_items": cart_items,
        "total": subtotal,
        "discount_total": discount_total,
    })
            
def cart_count(request):
    cart = request.session.get("cart", {})

    total_items = 0

    for qty in cart.values():
        total_items += int(qty or 0)

    return {"cart_count": total_items}

from django.http import JsonResponse

def update_cart(request, product_id, action):
    cart = request.session.get("cart", {})
    pid = str(product_id)

    if pid in cart:
        if action == "increase":
            cart[pid] = int(cart.get(pid, 0)) + 1

        elif action == "decrease":
            cart[pid] = int(cart.get(pid, 0)) - 1

            if cart[pid] <= 0:
                del cart[pid]

    request.session["cart"] = cart
    request.session.modified = True

    return JsonResponse({"status": "ok"})

def clear_cart(request):
    request.session["cart"] = {}
    request.session.modified = True
    return redirect("cart")
    
    # =======================
# REMOVE ITEM
# =======================

def remove_from_cart(request, product_id):
    cart = request.session.get("cart", {})
    pid = str(product_id)

    cart.pop(pid, None)

    request.session["cart"] = cart
    request.session.modified = True

    return JsonResponse({"status": "ok"})

# =======================
# PRODUCTS
# =======================
def product_search(request):
    query = request.GET.get('q', '')
    results = Product.objects.filter(name__icontains=query) if query else Product.objects.none()

    return render(request, 'search_results.html', {
        'query': query,
        'results': results
    })

def product_list(request):
    categories = Category.objects.filter(parent=None)
    products = Product.objects.filter(status='active').select_related('category')

    q = request.GET.get('q', '')
    selected_category = request.GET.get('category')

    if q:
        products = products.filter(
            Q(name__icontains=q) |
            Q(sku__icontains=q)
        )

    if selected_category:
        products = products.filter(
            Q(category_id=selected_category) |
            Q(category__parent_id=selected_category)
        )
        
    # ✅ apply discount to each product
    final_products = []

    for p in products:
        final_price, discount_obj, percent = calculate_discounted_price(p)
        p.final_price = float(final_price)
        p.discount_percent = float(percent) if percent else 0
        p.discount_obj = discount_obj
        final_products.append(p)

    paginator = Paginator(final_products, 12)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'user/product/list.html', {
        'categories': categories,
        'products': page_obj,
        'page_obj': page_obj,
        'q': q,
        'selected_category': selected_category,
    })

def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

    final_price, discount_obj, percent = calculate_discounted_price(product)

    return JsonResponse({
        "id": product.id,
        "name": product.name,
        "image": product.img.url if product.img else "",
        "price": float(final_price),
        "original_price": float(product.selling_price or 0),
        "discount_percent": float(percent) if percent else 0,
        "stock_qty": product.stock_qty,
        "status": product.status,
    })
# =======================
# DASHBOARD (STORE FRONT)
# =======================

def dashboard(request):
    products = Product.objects.filter(status='active')[:12]

    sale_products = []

    for p in products:
        final_price, discount_obj, percent = calculate_discounted_price(p)
        p.final_price = float(final_price)
        p.discount_percent = float(percent) if percent else 0

        if discount_obj:
            sale_products.append(p)

    return render(request, "user/dashboard.html", {
        "sale_products": sale_products
    })

def checkout_view(request):
    cart = request.session.get("cart", {})

    cart_items = []
    subtotal = 0

    for product_id, qty in cart.items():
        product = Product.objects.get(id=product_id)

        price = float(product.selling_price or 0)
        line_total = price * qty
        subtotal += line_total

        cart_items.append({
            "product": product,
            "quantity": qty,
            "line_total": line_total
        })

    return render(request, "user/checkout.html", {
        "cart_items": cart_items,
        "subtotal": subtotal,
        "total_amount": subtotal,
        "payment_choices": Sale.PAYMENT_CHOICES
    })

@transaction.atomic
def process_sale(request):
    if request.method == "POST":

        cart = request.session.get("cart", {})

        if not cart:
            return redirect("checkout")

        full_name = request.POST.get("full_name")
        phone = request.POST.get("phone")
        payment_method = request.POST.get("payment_method")

        customer, _ = Customer.objects.get_or_create(
            full_name=full_name,
            phone=phone
        )

        subtotal = 0

        sale = Sale.objects.create(
            customer=customer,
            subtotal=0,
            total_amount=0,
            payment_method=payment_method,
            status="completed"
        )

        for product_id, qty in cart.items():
            product = Product.objects.get(id=product_id)

            price = float(product.selling_price or 0)
            line_total = price * qty
            subtotal += line_total

            SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=qty,
                unit_price=price,
                line_total=line_total
            )

            # stock update
            product.stock_qty = (product.stock_qty or 0) - qty
            product.save()

        sale.subtotal = subtotal
        sale.total_amount = subtotal
        sale.save()

        # CLEAR CART AFTER SALE
        request.session["cart"] = {}
        request.session.modified = True

        return redirect("success_page")

    return redirect("checkout")
    
        # =======================
# STATIC PAGES
# =======================

def about_us(request):
    return render(request, 'user/about.html')


def contact_us(request):
    return render(request, 'user/contact.html')