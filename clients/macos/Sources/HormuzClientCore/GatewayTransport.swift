import Foundation

public struct GatewayReply: Sendable {
    public let status: Int
    public let data: Data
    public init(status: Int, data: Data) { self.status = status; self.data = data }

    public func decode<T: Decodable>(_ type: T.Type) throws -> T {
        do { return try GatewayJSON.decoder().decode(type, from: data) }
        catch { throw ClientError.invalidResponse }
    }
}

public protocol GatewayTransport: Sendable {
    func request(profile: ConnectionProfile, path: String, body: Data?, accessToken: String?) async throws -> GatewayReply
}

private final class NoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

public final class HTTPGatewayTransport: GatewayTransport, @unchecked Sendable {
    private let session: URLSession
    private let responseLimit = 128 * 1024

    public init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 15
        configuration.httpMaximumConnectionsPerHost = 2
        session = URLSession(configuration: configuration, delegate: NoRedirect(), delegateQueue: nil)
    }

    deinit { session.invalidateAndCancel() }

    public func request(profile: ConnectionProfile, path: String, body: Data? = nil,
                        accessToken: String? = nil) async throws -> GatewayReply {
        let origin = try profile.validated().gateway
        guard path.hasPrefix("/v1/"), !path.contains("?"), !path.contains("#"), !path.contains(".."),
              let url = URL(string: origin + path) else { throw ClientError.invalidGateway }
        var request = URLRequest(url: url)
        request.httpMethod = body == nil ? "GET" : "POST"
        request.httpBody = body
        request.httpShouldHandleCookies = false
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let accessToken {
            guard SessionRecord.validToken(accessToken, prefix: "hox_a_") else { throw ClientError.invalidResponse }
            request.setValue("Bearer " + accessToken, forHTTPHeaderField: "Authorization")
        }
        do {
            let (bytes, rawResponse) = try await session.bytes(for: request)
            defer { bytes.task.cancel() }
            guard let response = rawResponse as? HTTPURLResponse, response.url == url else {
                throw ClientError.unexpectedRedirect
            }
            guard !(300...399).contains(response.statusCode) else { throw ClientError.unexpectedRedirect }
            guard response.mimeType?.lowercased() == "application/json" else { throw ClientError.invalidResponse }
            guard response.expectedContentLength <= responseLimit else { throw ClientError.responseTooLarge }
            var data = Data()
            for try await byte in bytes {
                guard data.count < responseLimit else { throw ClientError.responseTooLarge }
                data.append(byte)
            }
            return GatewayReply(status: response.statusCode, data: data)
        } catch let error as ClientError { throw error }
        catch is CancellationError { throw CancellationError() }
        catch { if Task.isCancelled { throw CancellationError() }; throw ClientError.gatewayUnavailable }
    }
}
