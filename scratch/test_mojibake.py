s = "ðð¤ðð¬ð¡ ððð¬"
print("repr:", repr(s))
for c in s:
    print(c, hex(ord(c)))

# Check common encodings
try:
    b = s.encode('latin1')
    print("Latin1 bytes:", b)
    print("Decoded utf8:", b.decode('utf-8', errors='replace'))
except Exception as e:
    print("Latin1 error:", e)

try:
    b = s.encode('cp1252')
    print("CP1252 bytes:", b)
    print("Decoded utf8:", b.decode('utf-8', errors='replace'))
except Exception as e:
    print("CP1252 error:", e)
