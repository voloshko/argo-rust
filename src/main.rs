use axum::{extract::Path, routing::get, Json, Router};
use serde::Serialize;

#[derive(Serialize)]
struct HelloResponse { message: String }

#[derive(Serialize)]
struct FibResponse { n: u64, result: u64 }

async fn hello() -> Json<HelloResponse> {
    Json(HelloResponse { message: "Hello Dennis!!!".to_string() })
}

async fn fibonacci(Path(n): Path<u64>) -> Json<FibResponse> {
    let result = if n == 0 { 0 } else {
        let (mut a, mut b) = (0u64, 1u64);
        for _ in 1..n { (a, b) = (b, a.saturating_add(b)); }
        b
    };
    Json(FibResponse { n, result })
}

#[tokio::main]
async fn main() {
    let app = Router::new()
        .route("/hello", get(hello))
        .route("/fibonacci/{n}", get(fibonacci));
    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await.unwrap();
    println!("Listening on 0.0.0.0:8080");
    axum::serve(listener, app).await.unwrap();
}
