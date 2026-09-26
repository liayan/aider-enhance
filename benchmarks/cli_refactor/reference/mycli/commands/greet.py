def greet(args):
    text = f"Hello, {args.name}!"
    print(text.upper() if args.upper else text)
    return 0
